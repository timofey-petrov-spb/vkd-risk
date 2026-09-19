# Независимая проверка времени GOES / iSWA

Шесть записей iSWA за **10 мая 2024, 00:00–00:25 UTC** сопоставлены с оригинальным
GOES-18 SGPS NetCDF по каналу **P500**. Временные метки и значения совпадают точно:
максимальная абсолютная разница равна нулю. Сравнивается датчик `sensor_units[0]`
(−X, западное направление при `yaw_flip_flag=0`); `satelliteProton` в iSWA — GOES-18.

**P500 не равен P10.** Эта проверка подтверждает согласованность временных меток
и происхождения выбранных шести точек. Она не валидирует поток ≥10 MeV, весь архив
или момент публикации данных. NetCDF содержит интегральный канал >500 MeV;
использовать его как готовый источник ≥10 MeV нельзя.

## Исходные файлы

Оба файла сохранены побайтно без преобразования:

- [NOAA GOES-18 NetCDF](https://data.ngdc.noaa.gov/platforms/solar-space-observing-satellites/goes/goes18/l2/data/sgps-l2-avg5m/2024/05/sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc)
  — локально `sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc`, 635 517 байт.
- [NASA iSWA HAPI, исходный JSON за полчаса](https://iswa.gsfc.nasa.gov/IswaSystemWebApp/hapi/data?id=goesp_part_flux_P5M&time.min=2024-05-10T00:00:00.0Z&time.max=2024-05-10T00:30:00.0Z&format=json)
  — локально `iswa_primary_20240510_0000_0030.json`, 2 807 байт.

SHA-256:

| Файл | SHA-256 |
| --- | --- |
| `sci_sgps-l2-avg5m_g18_d20240510_v3-0-2.nc` | `c4845f6da65b090525cac560037bc038dde9850a7334dbf76d4b4c944c59cb02` |
| `iswa_primary_20240510_0000_0030.json` | `f19686c797ef94a77e59002e33313d4cada4d96a764ee793b4af67cd8c900f75` |
| `timestamp-proof.json` | `6cf71a87bdb587e0baae174f3c0ed594af0b70a8dbe4d0a19587bdf46bac5286` |

`timestamp-proof.json` сохраняет все шесть времён и численных значений, ссылки и
результат сравнения. `stopDate` внутри исходного HAPI JSON — текущая граница базы
на момент ответа, а не дата публикации наблюдений 2024 года.

## Семантика времени

В NetCDF атрибут `time.long_name` задаёт начало периода усреднения. Такое же
правило указано для выходных интегральных потоков в [NOAA ATBD18, таблица 7,
печатная стр. 37](https://www.ngdc.noaa.gov/stp/space-weather/online-publications/stp_sii/spades/algorithm-theoretic-basis-documents/atbds_seiss/atbd_seiss18_integral-flux_v1-0.pdf)
и для входных ISO-меток интегральных потоков в [NOAA ATBD20, таблица 4,
печатная стр. 22](https://www.ngdc.noaa.gov/stp/space-weather/online-publications/stp_sii/spades/algorithm-theoretic-basis-documents/atbds_seiss/atbd_seiss20_event-detection_v1-1.pdf).
Совпадение с HAPI подтверждает, что в проверенных точках метки не сдвинуты.

Интервал `[t, t + 5 минут)` описывает усреднение измерения. Это не срок действия
предупреждения и не доказательство доступности данных к концу интервала.
`published_utc` остаётся неизвестным; использовать эту проверку для разрешения
строгого исторического прогноза нельзя.

## Воспроизведение

Из корня репозитория, только стандартная библиотека Python:

```sh
python data/goes_2024/validation/verify_timestamp_proof.py
```

Проверяются SHA-256 трёх файлов, схема доказательства, времена, единицы,
спутник и шесть значений исходного HAPI. NetCDF по умолчанию проверяется по хешу;
численный разбор HDF5 при этом не выполняется, что явно указано в результате.

Повторить также численное сравнение, если `h5py` уже есть в исследовательской среде:

```sh
python data/goes_2024/validation/verify_timestamp_proof.py --with-h5py
```

`h5py` — необязательный инструмент проверки; новые зависимости приложения не нужны.
