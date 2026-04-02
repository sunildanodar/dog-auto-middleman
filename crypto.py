import requests
from config import BLOCKCYPHER_TOKEN, MASTER_PRIVATE_KEY, MASTER_ADDRESS, CONFIRMATIONS_REQUIRED, BSC_RPC_URL, USDT_CONTRACT_ADDRESS, ENCRYPTION_KEY
from web3 import Web3
from cryptography.fernet import Fernet
import secrets, hashlib, base58, ecdsa, json
from ecdsa.util import sigencode_der_canonize

fernet = Fernet(ENCRYPTION_KEY)

def encrypt_key(key):
    return fernet.encrypt(key.encode()).decode()

def decrypt_key(enc_key):
    return fernet.decrypt(enc_key.encode()).decode()


def _safe_json_or_error(response):
    try:
        return response.json()
    except ValueError:
        return {
            "error": "Non-JSON response from provider",
            "status_code": response.status_code,
            "body": response.text[:500],
        }


def _is_limits_error(payload):
    if payload is None:
        return False
    if isinstance(payload, dict):
        text = json.dumps(payload).lower()
    else:
        text = str(payload).lower()
    return "limits reached" in text

# LTC functions
def _sha256(d): return hashlib.sha256(d).digest()
def _ripemd160(d):
    h = hashlib.new("ripemd160")
    h.update(d)
    return h.digest()
def _hash160(d): return _ripemd160(_sha256(d))
def _checksum(p): return _sha256(_sha256(p))[:4]


def _compressed_pubkey_from_private_hex(private_hex):
    private_bytes = bytes.fromhex(private_hex)
    sk = ecdsa.SigningKey.from_string(private_bytes, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    x, y = vk.pubkey.point.x(), vk.pubkey.point.y()
    return (b"\x02" if y % 2 == 0 else b"\x03") + x.to_bytes(32, "big")


def private_hex_to_ltc_address(private_hex):
    pub = _compressed_pubkey_from_private_hex(private_hex)
    payload = b"\x30" + _hash160(pub)
    return base58.b58encode(payload + _checksum(payload)).decode()

def generate_ltc_wallet():
    priv = secrets.token_bytes(32)
    sk = ecdsa.SigningKey.from_string(priv, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    x, y = vk.pubkey.point.x(), vk.pubkey.point.y()
    pub = (b"\x02" if y % 2 == 0 else b"\x03") + x.to_bytes(32, "big")
    payload = b'\x30' + _hash160(pub)
    addr = base58.b58encode(payload + _checksum(payload)).decode()
    return {"address": addr, "private": encrypt_key(priv.hex())}

def usd_to_ltc(amount_usd):
    try:
        price_data = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=litecoin&vs_currencies=usd",
            timeout=10,
        ).json()
        ltc_price = price_data["litecoin"]["usd"]
        return amount_usd / ltc_price
    except:
        return amount_usd / 100  # Fallback: assume ~$100/LTC


def detect_ltc_payment(address, amount_usd):
    amount_ltc = usd_to_ltc(amount_usd)
    minimum_ltc = max(amount_ltc * 0.99, 0)

    try:
        response = requests.get(
            f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/full?limit=50",
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        data = None

    if data:
        txs = data.get("txs", []) or []
        for tx in txs:
            confirmations = tx.get("confirmations", 0)
            txid = tx.get("hash")
            received_total = 0.0

            for output in tx.get("outputs", []):
                addresses = output.get("addresses", []) or []
                if address in addresses:
                    received_total += output.get("value", 0) / 1e8

            if received_total >= minimum_ltc:
                return True, confirmations, txid, received_total

    try:
        fallback = requests.get(
            f"https://sochain.com/api/v2/address/LTC/{address}",
            timeout=15,
        )
        fallback.raise_for_status()
        payload = fallback.json()
        if payload.get("status") != "success":
            return False, 0, None, 0.0

        for tx in payload.get("data", {}).get("txs", []):
            txid = tx.get("txid")
            details = requests.get(f"https://sochain.com/api/v2/tx/LTC/{txid}", timeout=15)
            details.raise_for_status()
            detail_payload = details.json()
            if detail_payload.get("status") != "success":
                continue

            confirmations = detail_payload["data"].get("confirmations", 0)
            received_total = 0.0
            for output in detail_payload["data"].get("outputs", []):
                if output.get("address") == address:
                    received_total += float(output.get("value", 0))

            if received_total >= minimum_ltc:
                return True, confirmations, txid, received_total
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return False, 0, None, 0.0

    return False, 0, None, 0.0

def send_ltc(to_address, amount, priv_key):
    priv = decrypt_key(priv_key)
    fee = amount * 0.02
    payout = max(amount - fee, 0)
    value_satoshis = int(payout * 1e8)
    if value_satoshis <= 0:
        return {"error": "Payout amount is too small after fee."}

    from_address = private_hex_to_ltc_address(priv)

    new_url = f"https://api.blockcypher.com/v1/ltc/main/txs/new?token={BLOCKCYPHER_TOKEN}"
    new_data = {
        "inputs": [{"addresses": [from_address]}],
        "outputs": [{"addresses": [to_address], "value": value_satoshis}],
        "change_address": from_address,
    }

    try:
        new_resp = requests.post(new_url, json=new_data, timeout=20)
    except requests.RequestException as exc:
        return {"error": f"Network error while creating tx: {exc}"}

    new_payload = _safe_json_or_error(new_resp)
    if new_resp.status_code >= 400:
        if _is_limits_error(new_payload):
            # Fallback path: micro endpoint sometimes works when tx/new is throttled.
            micro_url = f"https://api.blockcypher.com/v1/ltc/main/txs/micro?token={BLOCKCYPHER_TOKEN}"
            micro_data = {
                "from_private": priv,
                "to_address": to_address,
                "value_satoshis": value_satoshis,
            }
            try:
                micro_resp = requests.post(micro_url, json=micro_data, timeout=20)
                micro_payload = _safe_json_or_error(micro_resp)
                if micro_resp.status_code < 400:
                    tx_hash = None
                    if isinstance(micro_payload, dict):
                        tx_hash = micro_payload.get("tx_hash") or micro_payload.get("hash") or micro_payload.get("txid")
                    if tx_hash:
                        micro_payload["tx_hash"] = tx_hash
                    return micro_payload
                return micro_payload
            except requests.RequestException as exc:
                return {"error": f"Network error while using micro fallback: {exc}"}
        if isinstance(new_payload, dict):
            new_payload.setdefault("error", "Provider rejected unsigned transaction")
        return new_payload

    tosign = new_payload.get("tosign", []) if isinstance(new_payload, dict) else []
    if not tosign:
        return {"error": "Provider did not return data to sign.", "provider": new_payload}

    try:
        sk = ecdsa.SigningKey.from_string(bytes.fromhex(priv), curve=ecdsa.SECP256k1)
        pubkey_hex = _compressed_pubkey_from_private_hex(priv).hex()
        signatures = []
        pubkeys = []
        for item in tosign:
            digest = bytes.fromhex(item)
            sig = sk.sign_digest_deterministic(digest, hashfunc=hashlib.sha256, sigencode=sigencode_der_canonize)
            # BlockCypher expects DER signatures in hex here.
            signatures.append(sig.hex())
            pubkeys.append(pubkey_hex)
    except Exception as exc:
        return {"error": f"Signing failed: {exc}"}

    new_payload["signatures"] = signatures
    new_payload["pubkeys"] = pubkeys

    send_url = f"https://api.blockcypher.com/v1/ltc/main/txs/send?token={BLOCKCYPHER_TOKEN}"
    try:
        send_resp = requests.post(send_url, json=new_payload, timeout=20)
    except requests.RequestException as exc:
        return {"error": f"Network error while broadcasting tx: {exc}"}

    send_payload = _safe_json_or_error(send_resp)
    if send_resp.status_code >= 400:
        if _is_limits_error(send_payload):
            # Fallback path: micro endpoint sometimes works when tx/send is throttled.
            micro_url = f"https://api.blockcypher.com/v1/ltc/main/txs/micro?token={BLOCKCYPHER_TOKEN}"
            micro_data = {
                "from_private": priv,
                "to_address": to_address,
                "value_satoshis": value_satoshis,
            }
            try:
                micro_resp = requests.post(micro_url, json=micro_data, timeout=20)
                micro_payload = _safe_json_or_error(micro_resp)
                if micro_resp.status_code < 400:
                    tx_hash = None
                    if isinstance(micro_payload, dict):
                        tx_hash = micro_payload.get("tx_hash") or micro_payload.get("hash") or micro_payload.get("txid")
                    if tx_hash:
                        micro_payload["tx_hash"] = tx_hash
                    return micro_payload
                return micro_payload
            except requests.RequestException as exc:
                return {"error": f"Network error while using micro fallback: {exc}"}
        if isinstance(send_payload, dict):
            send_payload.setdefault("error", "Provider rejected signed transaction")
        return send_payload

    if isinstance(send_payload, dict) and isinstance(send_payload.get("tx"), dict):
        tx_hash = send_payload["tx"].get("hash")
        if tx_hash:
            send_payload["tx_hash"] = tx_hash
    return send_payload

def sweep_ltc_to_master(priv_key):
    priv = decrypt_key(priv_key)
    url = f"https://api.blockcypher.com/v1/ltc/main/txs/micro?token={BLOCKCYPHER_TOKEN}"
    data = {"from_private": priv, "to_address": MASTER_ADDRESS, "value_satoshis": -1}
    try:
        response = requests.post(url, json=data, timeout=20)
    except requests.RequestException as exc:
        return {"error": f"Network error while sweeping LTC: {exc}"}
    return _safe_json_or_error(response)

# BEP20 USDT functions
w3 = Web3(Web3.HTTPProvider(BSC_RPC_URL))

def generate_bep20_wallet():
    account = w3.eth.account.create()
    return {"address": account.address, "private": encrypt_key(account.key.hex())}

def detect_usdt_payment(address, amount):
    # Simplified: check balance
    contract = w3.eth.contract(address=Web3.to_checksum_address(USDT_CONTRACT_ADDRESS), abi=[{"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"}])
    balance = contract.functions.balanceOf(Web3.to_checksum_address(address)).call()
    usdt_balance = balance / 10**18  # USDT has 18 decimals
    if usdt_balance >= amount:
        # For simplicity, assume confirmed if balance >= amount
        return True, 1  # Assume 1 confirmation for now
    return False, 0

def send_usdt(to_address, amount, priv_key):
    priv = decrypt_key(priv_key)
    account = w3.eth.account.from_key(priv)
    contract = w3.eth.contract(address=Web3.to_checksum_address(USDT_CONTRACT_ADDRESS), abi=[{"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}])
    tx = contract.functions.transfer(Web3.to_checksum_address(to_address), int(amount * 10**18)).build_transaction({
        'from': account.address,
        'gas': 200000,
        'gasPrice': w3.eth.gas_price,
        'nonce': w3.eth.get_transaction_count(account.address),
    })
    signed = w3.eth.account.sign_transaction(tx, priv)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    return tx_hash.hex()

def sweep_usdt_to_master(priv_key):
    # Similar to send, but to master
    priv = decrypt_key(priv_key)
    account = w3.eth.account.from_key(priv)
    contract = w3.eth.contract(address=Web3.to_checksum_address(USDT_CONTRACT_ADDRESS), abi=[{"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"}])
    balance = contract.functions.balanceOf(account.address).call()
    if balance > 0:
        tx = contract.functions.transfer(Web3.to_checksum_address(MASTER_ADDRESS), balance).build_transaction({
            'from': account.address,
            'gas': 200000,
            'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(account.address),
        })
        signed = w3.eth.account.sign_transaction(tx, priv)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        return tx_hash.hex()
    return None
