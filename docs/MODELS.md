# Model AI

| | Detektor | Embedding |
|---|---|---|
| Model | YuNet `face_detection_yunet_2023mar.onnx` | SFace `face_recognition_sface_2021dec.onnx` |
| Sumber | https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet | https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface |
| Lisensi | MIT (diperiksa dari file LICENSE repositori) | Apache-2.0 (diperiksa dari file LICENSE repositori) |
| SHA-256 | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |
| Runtime | OpenCV DNN (`cv2.FaceDetectorYN`) | ONNX Runtime CPU (default) atau OpenCV DNN |
| Output | bbox + 5 landmark + skor | vektor 128-d, dinormalisasi L2 |
| Versi tercatat | `opencv-zoo/yunet-2023mar` | `opencv-zoo/sface-2021dec/128` (disimpan per referensi) |

## Alasan pemilihan

* Berjalan di CPU tanpa GPU dan tanpa layanan cloud.
* Lisensi permisif yang jelas (MIT/Apache-2.0) untuk prototipe rumah sakit.
* Model InsightFace (mis. `buffalo_l`/ArcFace) **tidak dipakai**: bobot pra-latih resminya hanya
  untuk riset non-komersial.
* Embedding ONNX Runtime dan OpenCV identik secara numerik (selisih maksimum 2.8e-6, cosine >
  0.9999; diuji di `tests/test_ai_models.py`), sehingga versi model tidak bergantung backend.

## Performa terukur (Intel Core i7-8665U, 8 thread, tanpa GPU, 2026-10-18)

| Tahap | Hasil |
|---|---|
| Kamera laptop 640x480 | ±22–28 fps |
| Deteksi YuNet 640x480 | ±23–26 ms/frame |
| Embedding SFace (ORT) | ±9 ms/wajah |
| Loop worker | dibatasi `WORKER_MAX_INFERENCE_FPS=10`; terukur 10.0 fps (tanpa wajah di depan kamera) |

Target 3–5 fps inferensi terpenuhi dengan margin. Angka dengan banyak wajah sekaligus dan pada
kamera RTSP 1080p belum diukur.

## Keterbatasan

* SFace dilatih pada wajah frontal; jarak 3–5 m dari kamera pintu masuk membutuhkan resolusi
  cukup (wajah ≥ 64 px). Untuk RTSP 1080p, pertimbangkan ROI atau input detektor lebih besar.
* Belum ada liveness/anti-spoofing.
* Ambang belum divalidasi pada populasi pasien lokal. Lakukan evaluasi FMR/FNMR dengan peserta
  uji yang memberi persetujuan sebelum produksi.

## Mengganti model

Implementasikan kelas dengan antarmuka `FaceDetector.detect(frame) -> list[Detection]` dan
`FaceEmbedder.align/embed_aligned`, ubah `MODEL_VERSION`. Referensi lama dengan versi berbeda
otomatis tidak dipakai untuk pencocokan (status `ERROR` jika worker mengirim versi lain); daftar
ulang referensi diperlukan.
