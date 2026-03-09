FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# ENTRYPOINT_SCRIPT 環境変数でどのスクリプトを実行するか切り替え
# main.py: 日次同期 / weekly_discord.py: 週次Discordレポート
ENV ENTRYPOINT_SCRIPT=main.py

CMD python $ENTRYPOINT_SCRIPT
