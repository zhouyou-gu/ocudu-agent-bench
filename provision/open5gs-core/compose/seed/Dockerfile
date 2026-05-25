# Open5GS subscriber-seeder image: a one-shot container that reads
# subscriber_db.csv and upserts the rows into the open5gs.subscribers
# MongoDB collection. Built locally by docker compose; not published.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY add_users.py ./
COPY subscriber_db.csv ./

ENV SUBSCRIBER_CSV=/app/subscriber_db.csv \
    MONGO_URI=mongodb://mongo:27017/ \
    MONGO_DB=open5gs

ENTRYPOINT ["python3", "/app/add_users.py"]
