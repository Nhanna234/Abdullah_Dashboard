# Working on this repository

Ali's Abdullah Dashboard: a Streamlit app (`app.py`) whose pages are drawn by a React + Vite front end
(`frontend/`, built into `frontend/dist`, which must be committed because Streamlit Cloud does not build it).

- Python does all the calculating: `app.py` (market breadth engine and the data sent to the website),
  `sectors.py` (sector rotation, index list, Rank(1,3,6)), `rs_leaders.py` (the Abu Abdullah RS screen),
  `lender_filter.py` (copied unchanged from github.com/Nhanna234/RS-on-Nifty; keep the two copies the same).
- The front end only draws: `frontend/src/App.jsx` (shell, tabs, requests to Python), `Breadth.jsx`,
  `Sectors.jsx`, `charts.jsx` (hand-made SVG charts), `ui.jsx` (colours, formats, small controls), `styles.css`.
- After changing `frontend/src`, run `cd frontend && npm install && npm run build` and commit `frontend/dist`.
- Offline testing: set `BREADTH_OFFLINE_DIR` to a folder of test files (indices.csv, nifty500.csv, stocks.csv,
  sectors.csv, constituents/, stocks_monthly.pkl) and `BREADTH_DEFAULT_PAGE` to `breadth` or `sectors`.
- Ali is new to coding: explain changes in plain language.
