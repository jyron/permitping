from sqlalchemy.orm import Session

from app import config
from app.db import repo
from app.db.models import Account
from app.services import emailer


def get_or_create_account(db: Session, email: str) -> Account:
    email = email.strip().lower()
    return repo.get_account_by_email(db, email) or repo.create_account(db, email)


def issue_token(db: Session, account: Account) -> str:
    return repo.create_login_token(db, account.id, config.LOGIN_TOKEN_DAYS).token


def send_login_link(db: Session, email: str) -> None:
    account = repo.get_account_by_email(db, email.strip().lower())
    # ponytail: silently no-op for unknown emails so the endpoint doesn't leak
    # which addresses have accounts.
    if account:
        emailer.send_account_link(account.email, issue_token(db, account))


def resolve_token(db: Session, token: str) -> Account | None:
    return repo.get_account_by_token(db, token)


def change_email(db: Session, account: Account, new_email: str) -> Account:
    return repo.update_account_email(db, account, new_email.strip().lower())
