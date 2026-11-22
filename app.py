"""Zahir Analytics: authenticated segmentation page backed only by Supabase."""
from __future__ import annotations

import time
from decimal import Decimal

import pandas as pd
import streamlit as st
from supabase import create_client
from supabase.lib.client_options import SyncClientOptions

from zahir_dashboard.backend import (
    DashboardError,
    SegmentationRepository,
    reference_checks,
    rupiah,
    settings_from_secrets,
)


def reset_page() -> None:
    st.session_state["invoice_page"] = 0


def change_page(delta: int) -> None:
    st.session_state["invoice_page"] = max(0, st.session_state.get("invoice_page", 0) + delta)


def logout() -> None:
    client = st.session_state.get("_supabase_client")
    try:
        if client is not None:
            client.auth.sign_out({"scope": "local"})
    except Exception:
        pass
    # Clear this browser session, including any previously displayed search/state.
    st.session_state.clear()


def authenticated_user(client):
    try:
        session = client.auth.get_session()
        if session is None:
            return None
        if session.expires_at and session.expires_at <= time.time() + 60:
            session = client.auth.refresh_session().session
        if session is None:
            return None
        client.postgrest.auth(session.access_token)
        response = client.auth.get_user()
        return response.user
    except Exception:
        logout()
        st.warning("Sesi tidak tersedia atau telah berakhir. Silakan masuk kembali.")
        return None


def render_login(client) -> None:
    st.subheader("Masuk untuk pengguna internal")
    st.caption("Gunakan akun Supabase Auth yang telah diberi izin baca oleh administrator.")
    with st.form("login", clear_on_submit=True):
        email = st.text_input("Email", max_chars=254)
        password = st.text_input("Kata sandi", type="password", max_chars=256)
        submitted = st.form_submit_button("Masuk")
    if submitted:
        if not email.strip() or not password:
            st.error("Isi email dan kata sandi.")
            return
        try:
            response = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
            if response.session is None:
                st.error("Login belum berhasil. Periksa akun dan konfirmasi email.")
                return
            client.postgrest.auth(response.session.access_token)
        except Exception:
            st.error("Login belum berhasil. Periksa kredensial atau ketersediaan layanan autentikasi.")
            return
        st.rerun()


def render_detail(repo: SegmentationRepository, choice, cluster_names):
    st.subheader("Detail Faktur")
    st.caption("Satu baris mewakili satu kelompok faktur lolos. Klaster mengikuti label pelanggan pada model terpilih.")
    try:
        # Server-side aggregation avoids the REST row cap and checks ID uniqueness.
        totals = repo.invoice_totals()
        count = repo.discover_detail(choice)
        if totals["invoices"] != count:
            raise DashboardError("Data detail berubah saat dibaca. Muat ulang untuk memperoleh cakupan yang konsisten.")
    except DashboardError as exc:
        st.warning("Akses atau kesiapan detail belum lengkap. " + str(exc))
        st.caption("Administrator perlu memeriksa fungsi audit total, izin SELECT, dan RLS. Data detail belum ditampilkan.")
        return None

    st.session_state.setdefault("invoice_page", 0)
    st.session_state.setdefault("company_query", "")
    with st.form("company_search"):
        draft = st.text_input(
            "Cari nama perusahaan", value=st.session_state["company_query"],
            max_chars=120, placeholder="Ketik sebagian nama perusahaan",
        )
        searched = st.form_submit_button("Cari perusahaan")
    if searched:
        st.session_state["company_query"] = draft.strip()
        reset_page()
    page_size = st.selectbox("Baris per halaman", [25, 50, 100], key="page_size", on_change=reset_page)
    try:
        result = repo.detail_page(
            choice, st.session_state["company_query"], st.session_state["invoice_page"], page_size
        )
        last_page = max(0, (result.count - 1) // page_size)
        if result.page > last_page:
            st.session_state["invoice_page"] = last_page
            st.rerun()
        if result.rows:
            display_rows = []
            for row in result.rows:
                if row["Klaster"] not in cluster_names:
                    raise DashboardError("Kode klaster detail tidak terdapat pada ringkasan model terpilih.")
                display_rows.append({
                    **row, "Grand total (Rp)": float(row["Grand total (Rp)"]),
                    "Segmen": cluster_names[row["Klaster"]],
                })
            st.dataframe(
                pd.DataFrame(display_rows), hide_index=True, width="stretch",
                column_config={"Grand total (Rp)": st.column_config.NumberColumn(format="localized")},
            )
            start = result.page * page_size + 1
            end = min((result.page + 1) * page_size, result.count)
            st.caption(f"Menampilkan {start:,}–{end:,} dari {result.count:,} kelompok faktur hasil pencarian.")
        else:
            st.info("Tidak ada faktur yang cocok dengan pencarian nama perusahaan.")
        st.caption(
            f"Halaman {result.page + 1} dari {last_page + 1} · "
            f"Urutan tetap: ID kelompok faktur · Cakupan keseluruhan: {totals['invoices']:,} kelompok."
        )
        previous, following, _ = st.columns([1, 1, 4])
        previous.button(
            "Sebelumnya", key="previous_page", disabled=result.page == 0,
            on_click=change_page, args=(-1,),
        )
        following.button(
            "Berikutnya", key="next_page", disabled=result.page >= last_page,
            on_click=change_page, args=(1,),
        )
    except DashboardError as exc:
        st.error(str(exc))
    return totals


def main() -> None:
    st.set_page_config(page_title="Segmentasi Pelanggan | Zahir Analytics", page_icon="📊", layout="wide")
    st.title("Zahir Analytics")
    st.header("Segmentasi Pelanggan")
    st.caption("Profil eksploratif dari kelompok faktur lolos cleaning. Nama standar masih merupakan label pelanggan sementara.")
    try:
        settings = settings_from_secrets(st.secrets)
    except DashboardError as exc:
        st.error(str(exc))
        return
    except Exception:
        st.error("Streamlit secrets belum tersedia atau formatnya belum valid. Gunakan contoh konfigurasi proyek.")
        return
    if "_supabase_client" not in st.session_state:
        try:
            # Never share an authenticated client via cache_resource or a module global.
            st.session_state["_supabase_client"] = create_client(
                settings.url,
                settings.key,
                options=SyncClientOptions(
                    auto_refresh_token=False,
                    persist_session=False,
            ),
)
        except Exception:
            st.error("Koneksi Supabase belum dapat dibuat. Periksa konfigurasi administrator.")
            return
    client = st.session_state["_supabase_client"]
    user = authenticated_user(client)
    if user is None:
        render_login(client)
        return
    st.sidebar.success("Sesi pengguna terautentikasi")
    st.sidebar.button("Keluar", on_click=logout)
    repo = SegmentationRepository(client, settings.columns)
    try:
        repo.authorize()
    except DashboardError as exc:
        st.error(str(exc))
        st.info("Ringkasan dan detail hanya tersedia untuk akun internal yang berwenang.")
        return
    st.sidebar.caption("Izin internal terverifikasi pada database.")
    try:
        choices = repo.discover_models()
        by_key = {choice.key: choice for choice in choices}
        if st.session_state.get("model_key") not in by_key:
            st.session_state.pop("model_key", None)
        if "kmeans_k4" not in by_key:
            st.warning("Ringkasan K-Means k=4 belum tersedia; hanya konfigurasi yang terbaca yang ditampilkan.")
        key = st.selectbox(
            "Model segmentasi", list(by_key), format_func=lambda value: by_key[value].title,
            key="model_key", on_change=reset_page,
        )
        choice = by_key[key]
        summary = repo.summary(choice)
        customer_count = sum(row["customers"] for row in summary)
        monetary = sum((row["monetary"] for row in summary), Decimal(0))
        a, b, c = st.columns(3)
        a.metric("Label pelanggan sementara", f"{customer_count:,}".replace(",", "."))
        b.metric("Total nominal model terpilih", rupiah(monetary))
        c.metric("Jumlah klaster", choice.k)
        st.caption(f"KPI hanya menjumlahkan {choice.title}; konfigurasi lain tidak ikut dijumlahkan.")
        if all(row.get("start") and row.get("end") and row.get("reference") for row in summary):
            scopes = {(row["start"], row["end"], row["reference"]) for row in summary}
            if len(scopes) == 1:
                start, end, reference = scopes.pop()
                st.caption(f"Periode sumber: {start} sampai {end}. Acuan Recency: {reference}.")
            else:
                st.warning("Konteks periode pada segmen tidak konsisten; periksa sumber.")
        st.subheader("Total Nominal per Klaster")
        chart = pd.DataFrame({
            "Klaster": [row["cluster"] for row in summary],
            "Total nominal (Rp)": [float(row["monetary"]) for row in summary],
        })
        st.bar_chart(chart, x="Klaster", y="Total nominal (Rp)", color="Klaster", sort=False)
        st.caption("Sumbu horizontal: kode klaster. Sumbu vertikal: rupiah dari grand total yang dihitung sekali per kelompok faktur.")
        st.dataframe(pd.DataFrame([{
            "Klaster": row["cluster"], "Nama segmen": row["name"],
            "Jumlah label": row["customers"],
            "Persentase label": 100 * row["customers"] / customer_count if customer_count else 0,
            "Total nominal": rupiah(row["monetary"]),
        } for row in summary]), hide_index=True, width="stretch")
        totals = render_detail(repo, choice, {row["cluster"]: row["name"] for row in summary})
        checks = reference_checks(summary, totals)
        with st.expander("Validasi cakupan terhadap notebook", expanded=not all(row["Sesuai"] for row in checks)):
            st.dataframe(pd.DataFrame(checks), hide_index=True, width="stretch")
            st.caption(
                "Acuan: 1.456 label per model, 2.874 kelompok faktur, dan Rp8.225.196.662,39. "
                "Nilai terbaca berasal dari Supabase dalam cakupan izin pengguna; perubahan data atau RLS dapat mengubah hasil."
            )
            if totals is None:
                st.info("Validasi jumlah dan total nominal detail masih menunggu akses/fungsi audit database.")
        if not all(row["Sesuai"] for row in checks):
            st.warning("Cakupan Supabase berbeda dari acuan notebook. Periksa impor dan izin baris sebelum menggunakan angka.")
    except DashboardError as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
