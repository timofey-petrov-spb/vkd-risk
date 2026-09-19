# Independent physics validation references

NASA 2024 LEO forecast, issued 2023-11-02: NTRS 20230015158.
Original PDF, hourly flux and seven-hour fluence files; unchanged bytes.
Moorhead et al. 2019 method PDF: NTRS 20190030373.
URLs and SHA-256 are in manifest.json. Git marks originals -text for cross-OS hashes.

These files are TEST/VALIDATION references, never runtime forecast inputs.
The 2024 forecast is not an observation. Its flux has energy thresholds and
unshielded facing-plate geometry, unlike our mass-limited tumbling plate.
Directly matching those flux numbers would be a dimensional/geometry error.
The reference has 8791 hourly rows, including seven hours at the end beyond 2024.
Method equations 2–3 and 2024 Table 2 have been checked against rendered PDF pages.

Run `python scripts/validate_physical_models.py`. Numerical pass does not establish
absolute physical flux accuracy. Read docs/methods/PHYSICAL_VALIDATION_2026-09-19.md.
