"""Constants for the Contact Energy integration."""

import hashlib
from datetime import timedelta
from typing import Final

DOMAIN: Final = "contact_energy"
DOMAIN_NAME: Final = "Contact Energy"

CONF_ACCOUNT_ID: Final = "account_id"
CONF_CONTRACT_ID: Final = "contract_id"
CONF_CONTRACT_ICP: Final = "contract_icp"
CONF_USAGE_DAYS: Final = "usage_days"

DEFAULT_USAGE_DAYS: Final = 10
MIN_USAGE_DAYS: Final = 3
MAX_USAGE_DAYS: Final = 31
CONTRACT_KEY_LENGTH: Final = 24
DEFAULT_CURRENCY: Final = "NZD"
UPDATE_INTERVAL: Final = timedelta(hours=8)

SENSOR_ACCOUNT_BALANCE: Final = "account_balance"
SENSOR_NEXT_BILL_AMOUNT: Final = "next_bill_amount"
SENSOR_NEXT_BILL_DATE: Final = "next_bill_date"
SENSOR_PAYMENT_DUE: Final = "payment_due"
SENSOR_PAYMENT_DUE_DATE: Final = "payment_due_date"
SENSOR_PREVIOUS_READING_DATE: Final = "previous_reading_date"
SENSOR_NEXT_READING_DATE: Final = "next_reading_date"
SENSOR_USAGE: Final = "usage"
SENSOR_KEYS: Final = (
    SENSOR_USAGE,
    SENSOR_ACCOUNT_BALANCE,
    SENSOR_NEXT_BILL_AMOUNT,
    SENSOR_NEXT_BILL_DATE,
    SENSOR_PAYMENT_DUE,
    SENSOR_PAYMENT_DUE_DATE,
    SENSOR_PREVIOUS_READING_DATE,
    SENSOR_NEXT_READING_DATE,
)

STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = f"{DOMAIN}.statistics"


def contract_digest(account_id: object, contract_id: object, icp: object) -> str:
    """Return an opaque stable digest for a Contact electricity contract."""
    identity = ":".join(str(value) for value in (account_id, contract_id, icp))
    return hashlib.sha256(identity.encode()).hexdigest()
