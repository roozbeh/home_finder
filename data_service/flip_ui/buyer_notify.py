"""Drain the durable inbox outbox via a local fixture sink or configured SMTP."""
import argparse
import json
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from buyer_submissions import Inbox


def file_sink(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def send(row):
        if row['mode'] != 'preview':
            raise ValueError('The local delivery sink accepts preview records only')
        target = path / f"{row['id']}.json"
        with target.open('w') as output:
            json.dump(row, output)
        target.chmod(0o600)

    return send


def smtp_sink():
    host = os.environ['BUYER_SMTP_HOST']
    sender = os.environ['BUYER_SMTP_FROM']
    recipient = os.environ['BUYER_SMTP_TO']
    username = os.environ.get('BUYER_SMTP_USERNAME')
    password = os.environ.get('BUYER_SMTP_PASSWORD')

    def send(row):
        if row['mode'] != 'approved':
            raise ValueError('SMTP delivery accepts approved mode only')
        message = EmailMessage()
        message['From'] = sender
        message['To'] = recipient
        message['Subject'] = 'New submission in Flip Finder'
        message['Message-ID'] = f"<{row['id']}@{sender.rsplit('@', 1)[-1]}>"
        message.set_content(f"Open your private Flip Finder inbox for submission {row['id']}.")
        with smtplib.SMTP_SSL(host, int(os.environ.get('BUYER_SMTP_PORT', '465')), timeout=15) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)

    return send


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument('--fixture-sink', type=Path)
    options.add_argument('--smtp', action='store_true')
    args = parser.parse_args()
    inbox = Inbox(args.database)
    inbox.initialize()
    sender = smtp_sink() if args.smtp else file_sink(args.fixture_sink)
    print(f'Delivered {inbox.deliver(sender)} inbox notifications.')
