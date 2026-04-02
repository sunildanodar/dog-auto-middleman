
import requests
from config import BLOCKCYPHER_TOKEN, MASTER_PRIVATE_KEY, MASTER_ADDRESS, CONFIRMATIONS_REQUIRED

def detect_payment(address, amount):
    url = f"https://sochain.com/api/v2/address/LTC/{address}"
    data = requests.get(url).json()

    if data["status"] != "success":
        return False, 0

    for tx in data["data"]["txs"]:
        txid = tx["txid"]
        details = requests.get(f"https://sochain.com/api/v2/tx/LTC/{txid}").json()

        if details["status"] != "success":
            continue

        conf = details["data"]["confirmations"]

        for out in details["data"]["outputs"]:
            if out["address"] == address and float(out["value"]) >= amount:
                return True, conf

    return False, 0

def send_ltc(to_address, amount):
    fee = amount * 0.02
    payout = amount - fee

    url = f"https://api.blockcypher.com/v1/ltc/main/txs/micro?token={BLOCKCYPHER_TOKEN}"
    data = {
        "from_private": MASTER_PRIVATE_KEY,
        "to_address": to_address,
        "value_satoshis": int(payout * 1e8)
    }

    return requests.post(url, json=data).json()

def sweep_to_master(private_key):
    url = f"https://api.blockcypher.com/v1/ltc/main/txs/micro?token={BLOCKCYPHER_TOKEN}"
    data = {
        "from_private": private_key,
        "to_address": MASTER_ADDRESS,
        "value_satoshis": -1
    }
    return requests.post(url, json=data).json()
