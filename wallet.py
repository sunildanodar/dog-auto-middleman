
import secrets, hashlib, base58, ecdsa

def _sha256(d): return hashlib.sha256(d).digest()
def _ripemd160(d):
    h = hashlib.new("ripemd160"); h.update(d); return h.digest()
def _hash160(d): return _ripemd160(_sha256(d))
def _checksum(p): return _sha256(_sha256(p))[:4]

def generate():
    priv = secrets.token_bytes(32)
    sk = ecdsa.SigningKey.from_string(priv, curve=ecdsa.SECP256k1)
    vk = sk.get_verifying_key()
    x, y = vk.pubkey.point.x(), vk.pubkey.point.y()
    pub = (b"\x02" if y % 2 == 0 else b"\x03") + x.to_bytes(32, "big")
    payload = b'\x30' + _hash160(pub)
    addr = base58.b58encode(payload + _checksum(payload)).decode()
    return {"address": addr, "private": priv.hex()}
