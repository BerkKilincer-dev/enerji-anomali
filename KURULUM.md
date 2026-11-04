# Kurulum: projeyi kendi bilgisayarında çalıştırmak

Bu adımları sırayla yapan herkes paneli açabilir. Python ya da model eğitimi gerekmez;
her şey Docker içinde çalışır. Toplam süre ilk seferde yaklaşık 15 dakika.

## 1. Kurulması gerekenler

| Program | Ne işe yarar | Nereden |
|---|---|---|
| Git | Projeyi GitHub'dan indirir | https://git-scm.com/download/win (kurulumda hep "Next") |
| Docker Desktop | Veritabanını, API'yi ve paneli çalıştırır | https://www.docker.com/products/docker-desktop/ |

Docker Desktop kurarken:

- "Use WSL 2" seçili kalsın. Kurulum bitince bilgisayarı yeniden başlat.
- Docker Desktop'ı aç ve sol altta **Engine running** yazana kadar bekle. Docker her
  kullanımda açık olmalı.
- "WSL needs updating" derse PowerShell'i yönetici olarak aç ve `wsl --update` yaz.
- "Virtualization" hatası verirse bilgisayarın BIOS ayarından sanallaştırmayı (Intel VT-x
  veya AMD-V) açman gerekir.

Repo gizliyse GitHub kullanıcı adını Berk'e ver; seni projeye eklemesi gerekir.

## 2. Projeyi indir ve çalıştır

PowerShell'i aç ve komutları **sırayla** yaz (her satırdan sonra Enter). Proje kullanıcı
klasörüne iner (`C:\Users\<adın>\enerji-anomali`):

```powershell
git clone https://github.com/BerkKilincer-dev/enerji-anomali.git
cd enerji-anomali
copy .env.example .env
docker compose up -d --build
```

Son komut ilk seferde birkaç dakika sürer, çünkü gerekli programları indirir. Bitince kurulumu
izle:

```powershell
docker compose logs -f setup
```

Veri iner, veritabanına yüklenir ve model sonuçları yazılır (yaklaşık 1 dakika). Ekranda
`published` ya da `already published` yazısını görünce `Ctrl+C` ile çık.

## 3. Paneli aç

1. Tarayıcıda **http://localhost:5173** adresini aç. Önce Wattwise'ın tanıtım sayfası gelir; sağ
   üstteki **Giriş yap**'a bas.
2. Giriş: `admin@enerji.local` / `Wtw.Yonetim!8426` (bütün işletmeleri görür). Bir işletmenin
   gözünden bakmak için ör. `otel@enerji.local` / `Wtw.Isletme!3157`; diğerleri
   `ozelkolej`, `ofisplazasi`, `kongremerkezi`, `universitekampusu`, `canlidemotesisi` (hepsi `@enerji.local`).
3. Genel Bakış açılıyor ve grafikte veri görünüyorsa kurulum tamam. Soldaki **İşletmeler**'de 5 demo
   işletme ve Canlı Demo Tesisi kart olarak görünür.
4. Canlı demo için soldan **Canlı İzleme**'ye geç ve **Canlı demoyu başlat**'a bas (admin
   hesabıyla). 1-2 dakika içinde ilk alarmlar düşer, sonra her dakika 1 saat ilerler.

API'nin tüm uç noktaları: http://localhost:8000/docs

## 4. Günlük kullanım

| Ne yapmak istiyorsun | Komut (proje klasöründe) |
|---|---|
| Kapatmak (veriler silinmez) | `docker compose down` |
| Tekrar açmak (birkaç saniye) | `docker compose up -d` |
| Ekibin son değişikliklerini almak | `git pull` sonra `docker compose up -d --build` |
| Neyin çalıştığını görmek | `docker compose ps` |
| Her şeyi silip sıfırdan kurmak | `docker compose down -v` sonra `docker compose up -d --build` |

## 5. Sorun çıkarsa

| Belirti | Sebep ve çözüm |
|---|---|
| `error during connect` veya `docker` komutu bulunamadı | Docker Desktop açık değil. Aç, "Engine running" yazısını bekle. |
| `port is already allocated` ve hata `5432` diyor | Bilgisayarında PostgreSQL kurulu. `notepad .env` ile dosyayı aç, `DB_PORT=5432` satırını `DB_PORT=55432` yap, `DATABASE_URL` satırındaki `5432`'yi de `55432` yap, kaydet ve `docker compose up -d` çalıştır. |
| `port is already allocated` ve hata `8000`, `5173` ya da `1883` diyor | Başka bir program o portu kullanıyor. O programı kapat ya da bilgisayarı yeniden başlat. |
| Şifre doğru ama giriş olmuyor, "çok fazla" deneme diyor | 5 yanlış denemeden sonra o e-posta 15 dakika kilitlenir. Bekle ya da `docker compose restart api` çalıştır. |
| "API'ye ulaşılamıyor" yazıyor | `docker compose ps` çalıştır; `api` satırı `running` olmalı. Değilse `docker compose logs api` çıktısını grupta paylaş. |
| Panel açılıyor ama anomali yok, israf 0 | Kurulum bitmemiş. `docker compose logs setup` ile kontrol et, bir dakika bekle. |
| Canlı İzleme "servis çalışmıyor" diyor | `docker compose up -d` çalıştır; düzelmezse `docker compose logs live-demo` çıktısını grupta paylaş. |
| Hiçbiri değil | Çalıştırdığın komutu ve hata mesajının tamamını grupta paylaş. |

## 6. Kod yazacaklar için (isteğe bağlı)

Sadece paneli kullanacaksan bu bölümü atla.

- **Python ile çalışanlar** (veri, ML, backend): [Python 3.12](https://www.python.org/downloads/)
  kur, kurarken "Add python.exe to PATH" kutusunu işaretle. Sonra proje klasöründe:

  ```powershell
  python -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements-dev.txt
  ```

  `activate` "running scripts is disabled" derse bir kere
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` yaz ve tekrar dene.
- **Panel üzerinde çalışanlar**: ayrıca bir şey kurmaya gerek yok; panel Streamlit ile Docker
  içinde çalışır, `dashboard/` klasöründeki değişiklik tarayıcı yenilenince görünür.
- **Kod editörü**: [VS Code](https://code.visualstudio.com) önerilir.

Kod yazma ve GitHub'a gönderme kuralları README'deki "Çalışma şekli" bölümünde.
