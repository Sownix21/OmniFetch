# File From Link attribution

The Cobalt routing and Instagram media handling in `providers/` adapt ideas
and portions of the File From Link reference supplied in `SRC/`, including
`app/cobalt.py`, `app/downloader.py` and `app/ig.py`. The Aparat watch-page
fallback also adapts the approach in `app/downloader.py`.

Original project: File From Link, Reza Shakeri
Original repository: https://github.com/rzashakeri/file-from-link

The supplied reference includes this license notice, reproduced verbatim:

> MIT License
> Copyright (c) 2026 Reza Shakeri
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, subject to the standard MIT terms.

OmniFetch's adaptation adds its own provider interfaces, restricted HTTP
transport, per-job budgets, cancellation handling and delivery reporting.
The reference folder itself is excluded from publication and runtime imports.
