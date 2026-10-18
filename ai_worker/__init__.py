"""AI worker pengenalan wajah: kamera, deteksi, tracking, kualitas, embedding.

Paket ini sengaja tidak bergantung pada Django agar bisa dijalankan sebagai
proses terpisah. Modul ``matcher``, ``embedding``, ``detector`` dan
``quality`` juga dipakai oleh Django untuk registrasi dan pencocokan.
"""
