"""Read-only reconciliation of local exports; not a live Supabase test."""
from pathlib import Path
from decimal import Decimal
import json
import pandas as pd


def verify(root=Path(__file__).resolve().parents[1]):
    folder = root / "outputs" / "final_dataset"
    summary = pd.read_excel(folder / "Hasil_Segmentasi_Pelanggan_Zahir.xlsx", sheet_name="segment_summary")
    detail = pd.read_csv(folder / "faktur_lolos_dengan_klaster.csv",
                         usecols=["id_samaran", "id_kelompok_faktur", "grand_total_rp"],
                         dtype=str, encoding="utf-8-sig")
    amount = lambda values: sum((Decimal(str(x)) for x in values), Decimal(0))
    expected = Decimal("8225196662.39")
    result = {}
    for model, k in [("K-Means", 4), ("GMM", 3), ("GMM", 4)]:
        rows = summary[(summary.model == model) & (summary.k == k)]
        total = amount(rows.total_Monetary_Rp)
        assert len(rows) == k and rows.jumlah_label.sum() == 1456
        assert abs(total - expected) <= Decimal(".01")
        result[f"{model} k={k}"] = {"labels": int(rows.jumlah_label.sum()), "monetary": str(total)}
    assert len(detail) == 2874 and detail.id_samaran.nunique() == 1456
    assert detail.id_kelompok_faktur.is_unique and detail.notna().all().all()
    total = amount(detail.grand_total_rp)
    assert abs(total - expected) <= Decimal(".01")
    result["local_detail"] = {"invoices": len(detail), "labels": detail.id_samaran.nunique(), "monetary": str(total)}
    return result


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
