#!/usr/bin/env bash
# Repo oluşturulduktan sonra etiketleri kurar. GitHub CLI (gh) gerekir.
# Kullanım: bash scripts/github_kurulum.sh
set -e
create() { gh label create "$1" --color "$2" --description "$3" --force; }
create veri            1f6feb "Veri mühendisliği"
create ml              8250df "Model ve değerlendirme"
create backend         0e8a16 "API, servisler, entegrasyon"
create frontend        fbca04 "Dashboard"
create is-gelistirme   d93f0b "Pazar, müşteri, fiyat, pitch"
create bloke           b60205 "İlerleyemiyor, yardım gerekli"
create oncelik-yuksek  000000 "Bu hafta mutlaka bitmeli"
echo "Etiketler hazır."
