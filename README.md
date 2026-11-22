# Segmentasi Pelanggan Zahir

Analisis faktur penjualan Zahir menggunakan RFM dan Gaussian Mixture Model.

## Dashboard Segmentasi Pelanggan

Halaman Streamlit membaca Supabase saja. Ekspor lokal bukan sumber pengganti
ketika koneksi database gagal. Jalankan dari direktori proyek:

```powershell
.\venv_segmentasi_zahir\Scripts\python.exe -m pip install -r requirements-dashboard.txt
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
.\venv_segmentasi_zahir\Scripts\python.exe -m streamlit run app.py
```

Isi URL dan publishable key proyek pada secrets lokal. Legacy anon JWT key juga
didukung; secret/service-role key ditolak. File secrets asli diabaikan Git.
Masuk melalui akun email/password Supabase Auth. Tidak ada pendaftaran atau
pemberian izin melalui dashboard. Client terpisah untuk setiap sesi browser.

### Persiapan database oleh administrator

1. Tinjau SQL baca-saja [inspect_segmentation_schema.sql](sql/inspect_segmentation_schema.sql).
   Periksa nama kolom, tipe numerik, label model, RLS, dan grants.
2. Tinjau [segmentation_internal_read.sql](sql/segmentation_internal_read.sql)
   sebelum menerapkannya. Skrip **belum diterapkan** oleh pekerjaan lokal.
   Sesuaikan identifier RPC jika skema berbeda. RLS tetap aktif. Allowlist
   privat dan restrictive SELECT guard mencegah kebijakan authenticated yang
   lebih luas melewati izin internal. Skrip tidak memberi hak tulis.
   Periksa juga grants dan policies yang sudah ada sebelum/sesudah penerapan.
3. Buat/konfirmasi pengguna di Supabase Auth melalui administrator, lalu
   tambahkan UUID yang disetujui ke zahir_internal.segmentation_readers sesuai
   contoh SQL. Allowlist awal kosong. Metadata pengguna bukan sumber izin.
   Menonaktifkan entri mencabut izin pada permintaan berikutnya.
4. Uji akun internal yang disetujui, akun authenticated tanpa izin, dan
   permintaan anon langsung. Hanya pengguna internal berwenang boleh membaca
   kedua tabel atau RPC total.

Dashboard memerlukan has_segmentation_access() dan segmentation_invoice_totals().
RPC total berjalan sebagai pemanggil dengan RLS, menghitung di server tanpa
batas REST 1.000 baris. Jika izin/RPC belum tersedia, halaman memberi status akses.

Skema ekspor lokal: segment_summary memakai model, k, cluster_code, nama_segmen,
jumlah_label, total_Monetary_Rp. Label model adalah K-Means dan GMM.
Skema langsung Supabase **belum diperiksa tanpa kredensial**. Saat berjalan,
halaman memeriksa kolom serta label aktual, termasuk kapitalisasi kolom SQL.
Pemetaan kolom opsional tersedia pada contoh secrets. Skema yang tidak cocok
ditampilkan sebagai kesalahan.

K-Means k=4 adalah pilihan awal, dengan GMM k=3 dan k=4 sebagai pembanding.
KPI difilter berdasarkan pasangan model/k di server. Pencarian perusahaan
menggunakan ILIKE di server; karakter persen dan garis bawah dianggap teks
literal. Detail diurutkan berdasarkan ID kelompok faktur dan memakai pagination
25/50/100 baris. Pastikan ID unik; RPC total memeriksa duplikasinya.
Satu baris detail berarti satu kelompok faktur, bukan satu item.

### Pengujian lokal

```powershell
.\venv_segmentasi_zahir\Scripts\python.exe -m unittest discover -s tests -v
.\venv_segmentasi_zahir\Scripts\python.exe tools/verify_local_segmentation.py
```

Tes memakai perusahaan sintetis tanpa kredensial atau identitas asli. Cakupan:
pilihan model/KPI, filter server, pagination, skema, penolakan secret key,
login/logout, dan penolakan pengguna tanpa izin. Verifier membaca ekspor lokal
secara read-only dan hanya mencetak agregat: 1.456 label per model, 2.874
kelompok faktur, Rp8.225.196.662,39.

Masih memerlukan kredensial: skema/isi Supabase aktual, login dan refresh sesi
nyata, RLS/GRANT/RPC untuk ketiga jenis pengguna, pencarian dengan tanda baca,
pagination melewati 1.000 baris, serta rekonsiliasi total database. Tes mock
tidak membuktikan kebijakan SQL sudah terpasang di server.
