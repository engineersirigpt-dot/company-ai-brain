# P2 base image digest resolution — `python:3.11-slim` (linux/amd64)

**วันที่ resolve:** 2026-08-05  
**เครื่องมือ (trusted registry):** `docker buildx imagetools inspect` (Docker 29.5.3) — ไม่ได้เดา/รับ SHA จาก LLM

## ผล resolution

| field | value |
|---|---|
| tag | `docker.io/library/python:3.11-slim` |
| **index digest** (manifest list) | `sha256:1c06f14f1f45c37c7ba0563077e651f288b728eb4a227db32da92b52794ddb3e` |
| **platform digest (linux/amd64)** ← ใช้ pin | `sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553` |
| platform | linux/amd64 |
| version | 3.11.15-slim-trixie |
| base | debian:trixie-slim |
| created | 2026-08-05T01:05:00Z |

## การพิสูจน์ (self-consistent, ไม่เดา)
```
raw index JSON (docker buildx imagetools inspect python:3.11-slim --raw) = 10373 bytes
  sha256(raw) = 1c06f14f1f45c37c7ba0563077e651f288b728eb4a227db32da92b52794ddb3e   == index digest ✔
  raw.manifests[os=linux,arch=amd64].digest = sha256:78b39ef14d8e...4553          == platform digest ✔
```
→ raw content ที่ registry ส่งมา hash ตรงกับ index digest และภายในมี amd64 platform digest ตามค่าที่ pin

## ค่าที่ใช้ pin (PY_BASE)
```
python@sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553
```
เลือก **platform (amd64 child) digest** เพื่อให้ image identity fixed ในตัว digest เอง
(wrapper ยังล็อก `--platform linux/amd64` ซ้ำอีกชั้น)

_raw index JSON เก็บไว้นอก repo (build artifact) — ค่า digest ทั้งหมดยืนยันได้ซ้ำด้วยคำสั่งด้านบน_
