# LEAD benchmark reference

Reference scores to compare our models with. Our own day-level numbers are in the
"Day level" section of [reports/ml/results.md](../../reports/ml/results.md).

## LEAD 1.0 paper, Table 1

Source: Gulati and Arjunan, *LEAD1.0: A Large-scale Annotated Dataset for Energy Anomaly
Detection in Commercial Buildings*, [arXiv:2203.17256](https://arxiv.org/pdf/2203.17256)
(code and data: [samy101/lead-dataset](https://github.com/samy101/lead-dataset)).

| Model | F1 | Precision | Recall |
|---|---|---|---|
| CBLOF | 0.425 | 0.900 | 0.277 |
| Feature Bagging | 0.424 | 0.899 | 0.279 |
| KNN | 0.431 | 0.902 | 0.284 |
| HBOS | 0.397 | 0.896 | 0.258 |
| Isolation Forest | 0.413 | 0.895 | 0.270 |
| One-class SVM | 0.421 | 0.899 | 0.276 |
| LOF | 0.426 | 0.900 | 0.281 |
| MCD | 0.422 | 0.901 | 0.276 |

How the paper measures it:

- Unit: a 24-hour window per meter; a day is anomalous if it contains an anomalous hour.
- Models: unsupervised PyOD detectors on log(kWh + 1) and calendar features, z-scored.
- Data: all 1,413 electricity meters of 2016.
- Split: by sample, not by building. Normal days go 80/10/10 to train/val/test, anomalous
  days 10/20/70, so the test set is anomaly-rich.
- Threshold: picked on validation, applied to test.

## Other published results on LEAD

| Work | Reported F1 | Note |
|---|---|---|
| [DCGAN, ICLR 2025 workshop](https://www.climatechange.ai/papers/iclr2025/18) | 0.697 | generative model, own protocol |
| [Clustering-based supervised approach](https://avesis.kocaeli.edu.tr/yayin/e4eb46fc-a3fd-44a1-b574-cb835c90bce4/a-clustering-based-supervised-approach-for-anomaly-detection-in-building-energy-consumption) | 0.903 | supervised, own protocol |

## Comparing with our numbers

Our day-level metrics (`metrics.daily`) use the same day rule, but the setup differs:

- We split by building (140/30/30, seed 42), so test buildings are never seen in training.
- Our test set keeps the natural anomaly rate. The paper puts 70% of anomalous days in test,
  which raises precision for every model; compare F1 and recall, not precision.
- We train on LEAD small (200 buildings), not all 1,413 meters.
- Our detectors include supervised models and weather features; the paper's are
  unsupervised.

Treat the table as an order-of-magnitude reference, not a leaderboard. The paper's own
notebooks need the full dataset and PyOD and were not rerun here.
