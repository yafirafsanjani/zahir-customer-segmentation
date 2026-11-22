"""Supabase queries and validation; no local data fallback or shared user cache."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlparse


class DashboardError(Exception):
    def __init__(self, message: str, kind: str = "data"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Settings:
    url: str
    key: str
    columns: Mapping[str, Any]


def settings_from_secrets(secrets: Mapping[str, Any]) -> Settings:
    try:
        config = secrets["supabase"]
        url, key = str(config["url"]).rstrip("/"), str(config["publishable_key"])
    except (KeyError, TypeError):
        raise DashboardError(
            "Koneksi Supabase belum dikonfigurasi. Isi .streamlit/secrets.toml "
            "mengikuti secrets.toml.example.", "configuration"
        ) from None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DashboardError("URL Supabase harus berupa URL HTTPS yang valid.", "configuration")
    if key.startswith("sb_secret_"):
        raise DashboardError("Secret/service-role key tidak boleh dipakai oleh dashboard.", "configuration")
    if not key.startswith("sb_publishable_"):
        try:
            payload = key.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (IndexError, ValueError, UnicodeError):
            raise DashboardError("Gunakan publishable key atau legacy anon key Supabase.", "configuration") from None
        # This only identifies key type. Database authorization never trusts this decode.
        if claims.get("role") != "anon":
            raise DashboardError("Hanya publishable key atau legacy anon key yang diizinkan.", "configuration")
    return Settings(url, key, config.get("columns", {}))


def decimal_value(value: Any, label: str) -> Decimal:
    try:
        if value is None or isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError()
        return number
    except (InvalidOperation, ValueError):
        raise DashboardError(f"Nilai {label} pada Supabase tidak valid.") from None


def integer_value(value: Any, label: str) -> int:
    number = decimal_value(value, label)
    if number != number.to_integral_value() or number < 0:
        raise DashboardError(f"Nilai {label} harus bilangan bulat nonnegatif.")
    return int(number)


def rupiah(value: Decimal | int | float) -> str:
    return "Rp" + f"{Decimal(str(value)):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


SUMMARY_FIELDS = {
    "model": ("model",),
    "k": ("k",),
    "cluster": ("cluster_code",),
    "name": ("nama_segmen",),
    "customers": ("jumlah_label",),
    "monetary": ("total_Monetary_Rp",),
}
SUMMARY_OPTIONAL = {
    "start": ("analysis_start",),
    "end": ("analysis_end",),
    "reference": ("recency_reference",),
    "status": ("analysis_status",),
}
DETAIL_FIELDS = {
    "company": ("nama_klien",),
    "invoice_id": ("id_kelompok_faktur",),
    "date": ("tanggal_faktur",),
    "amount": ("grand_total_rp",),
}
CLUSTER_FIELDS = {
    "kmeans_k4": ("kmeans_k4_cluster",),
    "gmm_k3": ("gmm_k3_cluster",),
    "gmm_k4": ("gmm_k4_cluster",),
}
MODEL_SPECS = (
    ("kmeans_k4", "kmeans", 4, "K-Means k=4"),
    ("gmm_k3", "gmm", 3, "GMM k=3"),
    ("gmm_k4", "gmm", 4, "GMM k=4"),
)


def canonical_model(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return {
        "kmeans": "kmeans",
        "gmm": "gmm",
        "gaussianmixture": "gmm",
        "gaussianmixturemodel": "gmm",
        "gaussianmixturemodelgmm": "gmm",
    }.get(normalized, "")


def resolve_columns(
    sample: Mapping[str, Any],
    required: Mapping[str, tuple[str, ...]],
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate names against the live row before using them in filters/select."""
    result = {}
    overrides = overrides or {}
    for logical, candidates in required.items():
        if logical in overrides:
            actual = overrides[logical]
            if not isinstance(actual, str) or actual not in sample:
                raise DashboardError(f"Pemetaan kolom {logical} tidak ditemukan pada skema Supabase.", "schema")
        else:
            matches = [field for field in sample if field.casefold() in {x.casefold() for x in candidates}]
            if len(matches) != 1:
                raise DashboardError(
                    f"Kolom untuk {logical} belum cocok dengan skema Supabase. "
                    "Periksa skema dan pemetaan kolom pada secrets.", "schema"
                )
            actual = matches[0]
        result[logical] = actual
    return result


@dataclass(frozen=True)
class ModelChoice:
    key: str
    raw_model: str
    k: int
    title: str


@dataclass(frozen=True)
class DetailPage:
    rows: list[dict[str, Any]]
    count: int
    page: int
    page_size: int


def escape_like(term: str) -> str:
    # Treat company input as a literal substring, including % and _.
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SegmentationRepository:
    def __init__(self, client: Any, columns: Mapping[str, Any] | None = None):
        self.client = client
        self.columns = columns or {}
        self.summary_map: dict[str, str] = {}
        self.detail_map: dict[str, str] = {}
        self.summary_sample: dict[str, Any] = {}
        self.detail_sample: dict[str, Any] = {}
        self.detail_count: int | None = None

    def _table(self, name: str):
        return self.client.schema("public").table(name)

    @staticmethod
    def _execute(query: Any):
        try:
            return query.execute()
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code in {"PGRST301", "PGRST302"}:
                raise DashboardError("Sesi sudah tidak berlaku. Silakan masuk kembali.", "session") from None
            if code == "42501":
                raise DashboardError(
                    "Akses baca belum diizinkan oleh database. Periksa GRANT dan RLS untuk akun internal ini.",
                    "access",
                ) from None
            if code in {"PGRST202", "PGRST205", "42P01", "42883"}:
                raise DashboardError(
                    "Tabel atau fungsi akses belum tersedia pada API Supabase. "
                    "Konfigurasi database perlu ditinjau administrator.", "setup"
                ) from None
            if code in {"PGRST204", "42703"}:
                raise DashboardError("Skema kolom Supabase tidak sesuai dengan konfigurasi halaman.", "schema") from None
            # Never show raw SDK errors, query results, passwords, or tokens in UI.
            raise DashboardError("Supabase belum dapat dihubungi atau permintaan data gagal. Coba lagi.", "connection") from None

    def authorize(self) -> None:
        response = self._execute(self.client.rpc("has_segmentation_access"))
        if response.data is not True:
            raise DashboardError(
                "Akun terautentikasi, tetapi belum mendapat izin baca Segmentasi Pelanggan. "
                "Hubungi administrator untuk akses internal.", "access"
            )

    def discover_models(self) -> list[ModelChoice]:
        probe = self._execute(self._table("segment_summary").select("*", count="exact").limit(1))
        if not probe.data:
            raise DashboardError(
                "Tidak ada ringkasan yang dapat dibaca. Tabel mungkin kosong atau izin RLS belum lengkap.",
                "access",
            )
        self.summary_sample = probe.data[0]
        self.summary_map = resolve_columns(
            self.summary_sample, SUMMARY_FIELDS, self.columns.get("segment_summary", {})
        )
        fields = self.summary_map
        rows = []
        # Explicit stable order and bounded pages; not a single unbounded REST read.
        offset = 0
        while True:
            query = self._table("segment_summary").select(",".join(fields.values()), count="exact")
            for name in ("model", "k", "cluster"):
                query = query.order(fields[name])
            result = self._execute(query.range(offset, offset + 199))
            data = result.data or []
            rows.extend(data)
            if result.count is not None and len(rows) >= result.count:
                break
            if not data:
                break
            offset += len(data)
            if offset >= 10000:
                raise DashboardError("Ringkasan terlalu besar; periksa grain tabel segment_summary.")
        observed: dict[tuple[str, int], set[str]] = {}
        for row in rows:
            raw = str(row[fields["model"]])
            family = canonical_model(raw)
            k = integer_value(row[fields["k"]], "k")
            observed.setdefault((family, k), set()).add(raw)
        choices = []
        for key, family, k, title in MODEL_SPECS:
            values = observed.get((family, k), set())
            if len(values) > 1:
                raise DashboardError("Ada beberapa label model untuk konfigurasi yang sama. Periksa duplikasi ringkasan.")
            if values:
                choices.append(ModelChoice(key, next(iter(values)), k, title))
        if not choices:
            raise DashboardError("Konfigurasi K-Means k=4, GMM k=3, dan GMM k=4 belum ditemukan pada Supabase.")
        return choices

    def summary(self, choice: ModelChoice) -> list[dict[str, Any]]:
        fields = self.summary_map
        selected = list(fields.values())
        optional = {}
        for logical, candidates in SUMMARY_OPTIONAL.items():
            matches = [name for name in self.summary_sample if name.casefold() in {x.casefold() for x in candidates}]
            if len(matches) == 1:
                optional[logical] = matches[0]
                selected.append(matches[0])
        query = (
            self._table("segment_summary").select(",".join(selected))
            .eq(fields["model"], choice.raw_model).eq(fields["k"], choice.k)
            .order(fields["cluster"])
        )
        result = self._execute(query.range(0, 99))
        rows = []
        seen = set()
        for row in result.data or []:
            cluster = str(row[fields["cluster"]] or "")
            if not cluster.strip() or cluster in seen:
                raise DashboardError("Kode cluster kosong atau ganda pada konfigurasi terpilih.")
            seen.add(cluster)
            amount = decimal_value(row[fields["monetary"]], "total nominal")
            rows.append({
                "cluster": cluster,
                "name": str(row[fields["name"]]),
                "customers": integer_value(row[fields["customers"]], "jumlah label"),
                "monetary": amount,
                **{key: row.get(actual) for key, actual in optional.items()},
            })
        if len(rows) != choice.k:
            raise DashboardError(
                "Jumlah segmen yang terbaca tidak sesuai k. Periksa kelengkapan data dan cakupan RLS."
            )
        return rows

    def discover_detail(self, choice: ModelChoice) -> int:
        result = self._execute(
            self._table("faktur_lolos_dengan_klaster").select("*", count="exact").limit(1)
        )
        if not result.data:
            raise DashboardError(
                "Belum ada faktur yang dapat dibaca. Tabel mungkin kosong atau akses detail/RLS belum dikonfigurasi.",
                "access",
            )
        self.detail_sample = result.data[0]
        overrides = self.columns.get("faktur_lolos_dengan_klaster", {})
        if "cluster" in overrides:
            raise DashboardError(
                "Pemetaan klaster harus memakai kmeans_k4/gmm_k3/gmm_k4, bukan satu kolom cluster umum.",
                "schema",
            )
        self.detail_map = resolve_columns(self.detail_sample, DETAIL_FIELDS, overrides)
        cluster_map = resolve_columns(
            self.detail_sample, {choice.key: CLUSTER_FIELDS[choice.key]}, overrides
        )
        self.detail_map["cluster"] = cluster_map[choice.key]
        self.detail_count = integer_value(result.count, "jumlah faktur")
        return self.detail_count

    def detail_page(self, choice: ModelChoice, term: str, page: int, page_size: int) -> DetailPage:
        if page < 0 or page_size not in {25, 50, 100}:
            raise DashboardError("Ukuran atau nomor halaman tidak valid.")
        term = term.strip()
        if len(term) > 120:
            raise DashboardError("Pencarian perusahaan dibatasi 120 karakter.")
        fields = self.detail_map
        query = self._table("faktur_lolos_dengan_klaster").select(",".join(fields.values()), count="exact")
        if term:
            query = query.ilike(fields["company"], "%" + escape_like(term) + "%")
        query = query.order(fields["invoice_id"]).range(page * page_size, (page + 1) * page_size - 1)
        result = self._execute(query)
        count = integer_value(result.count, "jumlah hasil pencarian")
        data = result.data or []
        rows = []
        seen = set()
        for item in data:
            invoice_id = str(item[fields["invoice_id"]] or "")
            if not invoice_id.strip() or invoice_id in seen:
                raise DashboardError("ID kelompok faktur kosong/ganda; pagination belum dapat dipercaya.")
            seen.add(invoice_id)
            cluster = str(item[fields["cluster"]] or "")
            if not cluster.strip():
                raise DashboardError("Kode cluster detail belum terisi.")
            rows.append({
                "ID kelompok faktur": invoice_id,
                "Nama perusahaan": str(item[fields["company"]]),
                "Tanggal faktur": str(item[fields["date"]]),
                "Grand total (Rp)": decimal_value(item[fields["amount"]], "grand total"),
                "Klaster": cluster,
            })
        return DetailPage(rows, count, page, page_size)

    def invoice_totals(self) -> dict[str, Any]:
        result = self._execute(self.client.rpc("segmentation_invoice_totals"))
        if not isinstance(result.data, list) or len(result.data) != 1:
            raise DashboardError("Format hasil audit total faktur belum sesuai.", "schema")
        row = result.data[0]
        try:
            totals = {
                "invoices": integer_value(row["invoice_count"], "jumlah faktur"),
                "customers": integer_value(row["customer_count"], "jumlah ID faktur"),
                "unique_invoices": integer_value(row["unique_invoice_count"], "jumlah ID kelompok unik"),
                "monetary": decimal_value(row["total_grand_total"], "total grand total"),
            }
        except KeyError:
            raise DashboardError("Kolom RPC audit total faktur belum sesuai.", "schema") from None
        if totals["invoices"] != totals["unique_invoices"]:
            raise DashboardError("ID kelompok faktur ganda terdeteksi. Perbaiki data sebelum memakai pagination.")
        return totals


REFERENCE_CUSTOMERS = 1456
REFERENCE_INVOICES = 2874
REFERENCE_MONETARY = Decimal("8225196662.39")


def reference_checks(summary: list[dict[str, Any]], totals: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Reference values are audit targets, never substitutes for missing live data."""
    customers = sum(row["customers"] for row in summary)
    monetary = sum((row["monetary"] for row in summary), Decimal(0))
    checks = [
        {"Pemeriksaan": "Label model terpilih", "Terbaca": str(customers), "Acuan notebook": str(REFERENCE_CUSTOMERS), "Sesuai": customers == REFERENCE_CUSTOMERS},
        {"Pemeriksaan": "Monetary model terpilih", "Terbaca": rupiah(monetary), "Acuan notebook": rupiah(REFERENCE_MONETARY), "Sesuai": abs(monetary - REFERENCE_MONETARY) <= Decimal(".01")},
    ]
    if totals is not None:
        checks.extend([
            {"Pemeriksaan": "Kelompok faktur keseluruhan", "Terbaca": str(totals["invoices"]), "Acuan notebook": str(REFERENCE_INVOICES), "Sesuai": totals["invoices"] == REFERENCE_INVOICES},
            {"Pemeriksaan": "ID pada detail keseluruhan", "Terbaca": str(totals["customers"]), "Acuan notebook": str(REFERENCE_CUSTOMERS), "Sesuai": totals["customers"] == REFERENCE_CUSTOMERS},
            {"Pemeriksaan": "Nominal detail keseluruhan", "Terbaca": rupiah(totals["monetary"]), "Acuan notebook": rupiah(REFERENCE_MONETARY), "Sesuai": abs(totals["monetary"] - REFERENCE_MONETARY) <= Decimal(".01")},
            {"Pemeriksaan": "Detail vs ringkasan terpilih", "Terbaca": rupiah(totals["monetary"]), "Acuan notebook": rupiah(monetary), "Sesuai": abs(totals["monetary"] - monetary) <= Decimal(".01")},
        ])
    return checks
