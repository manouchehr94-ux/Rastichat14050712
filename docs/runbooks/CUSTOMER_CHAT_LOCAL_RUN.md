# Customer Chat Local Run

1. `docker-compose up -d`
2. `docker-compose exec backend python manage.py migrate`
3. `docker-compose exec backend python seed_data.py`
4. `cd packages/widget && npm run build && cd ..\..`
5. `python -m http.server 8081` (Root dir)
6. `cd apps/operator-dashboard && npm run dev`
7. Visit `http://localhost:8081/examples/plain-html/` and `http://localhost:3000`