"""
Angels Online Encryption Module
=================================
Two XOR cipher variants (confirmed from openao + third-party decryptor):

  CryptXOR: Static 16-byte repeating XOR.
    - Used for S->C encryption/decryption.
    - Key never changes within a session.

  CryptXORIV: Evolving 16-byte XOR with key mutation.
    - Used for C->S decryption on the server side.
    - After each decrypt, every DWORD of the key gets the
      (padded) payload length added. This makes the key
      stateful — it evolves per packet.
"""

import os
import struct
from abc import ABC, abstractmethod


class CryptBase(ABC):
    @abstractmethod
    def encrypt(self, data: bytes) -> bytes:
        pass

    @abstractmethod
    def decrypt(self, data: bytes) -> bytes:
        pass


class CryptNone(CryptBase):
    def encrypt(self, data: bytes) -> bytes:
        return data

    def decrypt(self, data: bytes) -> bytes:
        return data


class CryptXOR(CryptBase):
    """Static repeating 16-byte XOR cipher — used for S->C."""

    def __init__(self, key: bytes):
        if len(key) != 16:
            raise ValueError(f"XOR key must be 16 bytes, got {len(key)}")
        self.key = bytearray(key)

    def _xor(self, data: bytes) -> bytes:
        result = bytearray(len(data))
        for i in range(len(data)):
            result[i] = data[i] ^ self.key[i % 16]
        return bytes(result)

    def encrypt(self, data: bytes) -> bytes:
        return self._xor(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._xor(data)

    @staticmethod
    def generate_key() -> bytes:
        return os.urandom(16)


class CryptXORIV(CryptBase):
    """Evolving 16-byte XOR cipher — used for C->S.

    After each encrypt/decrypt call, each DWORD (4 bytes) of the key
    has the padded payload length added to it. This means the key
    changes after every packet, making it session-stateful.

    Confirmed from openao CryptXOR2 and third-party decryptor CryptXORIV.
    """

    def __init__(self, key: bytes):
        if len(key) != 16:
            raise ValueError(f"XOR key must be 16 bytes, got {len(key)}")
        self.key = bytearray(key)

    def _xor(self, data: bytes) -> bytes:
        result = bytearray(len(data))
        for i in range(len(data)):
            result[i] = data[i] ^ self.key[i % 16]
        return bytes(result)

    def _update_key(self, padded_len: int):
        """Add padded_len to each DWORD of the key."""
        for i in range(0, 16, 4):
            dword = struct.unpack_from('<I', self.key, i)[0]
            dword = (dword + padded_len) & 0xFFFFFFFF
            struct.pack_into('<I', self.key, i, dword)

    def encrypt(self, data: bytes) -> bytes:
        result = self._xor(data)
        self._update_key(len(data))
        return result

    def decrypt(self, data: bytes) -> bytes:
        result = self._xor(data)
        self._update_key(len(data))
        return result
