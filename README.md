# Rebelz

Telefonunda (Android + Termux) **kendi modelinle çalışan**, API kotası olmayan, otonom bir kişisel asistan.

- **Kota yok:** Model telefonda yerel çalışır (llama.cpp + Qwen2.5). Bulut API anahtarı, ücret veya istek limiti yok.
- **Otonom:** Asistan kendi araçlarını seçer ve sırayla kullanır: kabuk komutu, dosya okuma/yazma, web arama, web sayfası okuma, tarih/saat, kalıcı not.
- **Sınırsız adım:** `REBELZ_MAX_STEPS=0` (varsayılan) adım sınırı koymaz. Aynı araç çağrısının döngüye girmesi ise otomatik engellenir.
- **Bağımlılık yok:** Sadece Python standart kütüphanesi. Ek `pip install` gerekmez.
- **Türkçe:** Varsayılan olarak Türkçe konuşur ve Türkçe düşünür.

## Dürüst sınırlar

- Yerel 3B model, bulut modellerinden (GPT/Claude/Gemini sınıfı) çok daha zayıftır. Karmaşık akıl yürütmede hata yapabilir.
- Telefonda üretim hızı saniyede birkaç token civarındadır. Uzun görevler yavaş ilerler.
- Asistanın "tam otonom" çalışması için `REBELZ_AUTONOMY=full` gerekir. Bu modda kabuk komutları ve dosya yazma **onaysız** çalışır. Bunu sadece kendi telefonunda ve bilerek kullan.
- Dosya işlemleri `~/rebelz-workspace` klasörüyle sınırlıdır. Kabuk komutları ise telefonun izin verdiği her şeye erişebilir.

## Kurulum (Android / Termux)

1. Termux'u F-Droid'den kur (Play Store sürümü güncel değil).
2. Termux'ta şunu çalıştır (ilk kurulum ~20-40 dk, model ~2.1 GB):

   ```bash
   git clone https://github.com/helixooo183/Rebelz-asistan-agent.git
   cd Rebelz-asistan-agent
   bash scripts/termux_setup.sh
   ```

   Telefonunda en az 6 GB RAM önerilir. Daha az RAM'in varsa daha küçük model kullan:
   `MODEL_URL=https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf bash scripts/termux_setup.sh`
   (Bu durumda `REBELZ_MODEL_FILE` ile aynı dosya yolunu `start_llm.sh`'e de vermelisin.)

## Çalıştırma

Termux'ta iki oturum aç (Termux'ta yeni oturum: sol kenar menüsü → `NEW SESSION`):

```bash
# 1. oturum: model sunucusu (kapanmasın diye bırak)
bash scripts/start_llm.sh

# 2. oturum: web arayüzü
bash scripts/start_web.sh
```

Telefonun tarayıcısında **http://127.0.0.1:8787** adresini aç. Terminalden sohbet için: `bash scripts/start_cli.sh`.

Termux'un uyku modunda kapanmaması için bildirimden **Acquire wakelock** seçeneğini aç.

## Ayarlar (ortam değişkenleri)

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `REBELZ_AUTONOMY` | `confirm` | `confirm`: kabuk ve dosya yazma için onay ister. `full`: onaysız çalışır. |
| `REBELZ_MAX_STEPS` | `0` | Tek istekteki en fazla araç adımı. `0` = sınırsız. |
| `REBELZ_BASE_URL` | `http://127.0.0.1:8080/v1` | OpenAI uyumlu herhangi bir sunucu (yerel veya kendi sunucun). |
| `REBELZ_MODEL` | `local` | Sunucunun model adı. |
| `REBELZ_WORKSPACE` | `~/rebelz-workspace` | Dosya işlemlerinin sınırlı olduğu klasör. |
| `REBELZ_CONTEXT_CHARS` | `24000` | Konuşma geçmişinin karakter bütçesi (eskiler otomatik atılır). |
| `REBELZ_LLM_TIMEOUT` | `900` | Model yanıtı için saniye cinsinden zaman aşımı. |
| `REBELZ_WEB_HOST` | `127.0.0.1` | Web arayüzü adresi. Yerel ağa açmak için `0.0.0.0`. |
| `REBELZ_WEB_PORT` | `8787` | Web arayüzü portu. |
| `REBELZ_WEB_TOKEN` | (yok) | Web arayüzü erişim anahtarı. Yerel ağa açarken zorunludur. |

Örnek, tamamen otonom mod:

```bash
REBELZ_AUTONOMY=full bash scripts/start_web.sh
```

Yerel ağdaki başka bir cihazdan erişim (uzun bir anahtar seç):

```bash
REBELZ_WEB_HOST=0.0.0.0 REBELZ_WEB_TOKEN="$(head -c 24 /dev/urandom | base64)" bash scripts/start_web.sh
```

## Araçlar

| Araç | Ne yapar | Onay (confirm modunda) |
|---|---|---|
| `shell` | Çalışma alanında `sh` komutu çalıştırır | Evet |
| `write_file` | Çalışma alanına dosya yazar | Evet |
| `read_file`, `list_dir` | Çalışma alanını okur | Hayır |
| `web_search` | DuckDuckGo HTML sonuçlarını arar | Hayır |
| `web_fetch` | Bir web sayfasının metnini alır | Hayır |
| `now` | Telefondaki tarih ve saat | Hayır |
| `remember`, `recall` | Kalıcı not kaydet / ara | Hayır |

Web arayüzünde (`confirm` modu) onay penceresi yoktur. Bu yüzden yıkıcı işlemler reddedilir. Onaylı işlemler için terminal (`start_cli.sh`) kullan veya `full` modu seç.

## Terminal komutları

`/yardim`, `/araclar`, `/sifirla` (geçmişi temizle), `/cik`.

## Geliştirme ve testler

Harici paket gerekmez:

```bash
python -m unittest discover -s tests -v
```

Testler gerçek HTTP üzerinde çalışan sahte bir OpenAI uyumlu sunucu ve betik yazılmış bir model kullanır.

## Dosya yapısı

```
rebelz/
  agent.py    ajan döngüsü: model -> araç -> sonuç, döngü koruması, bağlam kırpma
  tools.py    araçlar ve çalışma alanı güvenliği
  llm.py      OpenAI uyumlu istemci (standart kütüphane)
  service.py  ajan fabrikası ve web oturumları
  cli.py      terminal arayüzü
  server.py   web sunucusu ve API
  static/     telefon için sohbet sayfası
scripts/      Termux kurulumu ve başlatma betikleri
tests/        birim ve uçtan uca testler
```
