# Modeller

Sahibi: ML geliştirici.

- `rules.py` — kural motoru (gece taban yükü, hafta sonu, ani sıçrama, sıfır okuma)
- `train_supervised.py` — LightGBM sınıflandırıcı
- `train_unsupervised.py` — beklenen tüketim regresyonu + residual skoru, Isolation Forest karşılaştırması
- `evaluate.py` — PR-AUC, F1, precision, recall; tüm modeller aynı tabloda

Kurallar:
- Bölme **bina bazında**; liste `data/splits.json` (veri mühendisi üretir, herkes aynısını kullanır).
- Sabit `random_state=42`.
- Her deneme MLflow'a kaydedilir.
