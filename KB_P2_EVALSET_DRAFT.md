# P2 synthetic eval-set — DRAFT (non-gated prep, รอ human/Codex review)

> **สร้างระหว่างรอ Codex confirm Slice 2** (ไม่แตะ Docker/model/benchmark) — data ล้วน
> **generator:** `p2_build_eval_set.py` (deterministic) → `p2_corpus.json` + `p2_eval_set.json`
> **สถานะ:** `label_status="draft"` ทุก case — **ต้อง human/Codex review ก่อนเลื่อนเป็น "human-reviewed"** (benchmark gate ตาม B2/M5)

## สรุป
| | |
|---|---|
| corpus points | 29 (frozen, policy-v1 payload ต่อ collection จริงจาก rbac_config) |
| cases | 76 (**dev 16 / test 60** — Codex ต้อง test ≥ 50) |
| categories | direct · thai-eng-mix · sibling-hard-negative · table-row · current-superseded · lexical-overlap-wrong-code · acronym-transliteration · negation · graded-multi |
| validation | `validate_corpus` = ผ่าน · `validate_ranking_eval_set` = 0 error (ยกเว้น label_status=draft ที่ตั้งใจ) |
| hashes | eval `ec452b5f…` · corpus `acf9b67e…` (freeze ก่อน benchmark) |

## หลักการ label (by-construction, ตรง Codex B2/M5)
- เขียน query + chunk คู่กัน → chunk ที่ตอบตรง = relevant (grade 3) ; graded-multi เพิ่ม related chunk (grade 2)
- **relevance authorized ต่อ role จริง** (query role ∈ payload allowed_roles ของ collection) — ผ่าน `is_authorized` (P1 policy)
- `relevant_sources` = exact set ของ source ที่ derive จาก relevant points
- hard negatives = chunk อื่นใน corpus ที่ lexical/semantic ใกล้แต่ไม่ใช่คำตอบ (เช่น WI-722 vs WI-721, QP-760 vs WI-423)

## ต้อง review อะไร (ก่อนเลื่อนเป็น human-reviewed)
1. คุณภาพ query/label: query เป็นคำถามจริงไหม, relevant chunk ตอบตรงไหม, grade เหมาะไหม
2. hard-negative แข็งพอไหม (ตอนนี้ synthetic templated — Codex ระบุ category ครบแล้ว)
3. corpus content สมจริงพอสำหรับ mechanics ไหม (นี่ = synthetic, ประกาศผลได้แค่ "mechanics on synthetic")
4. dev/test split + count (dev 16 พอเลือก N ไหม, test 60)

## ยังไม่ทำ (gated — รอ GO Slice 2)
- seed corpus นี้เข้า isolated Qdrant + candidate provider + cross-encoder rerank
- N sweep, metric run, durable evidence, M4 sentinel, p5b canary gate
- **no-answer / abstention suite แยก** (ยังไม่มี threshold contract — ไม่อยู่ใน ranking set นี้)

> DRAFT นี้ให้ Codex/พี่ตรวจ mechanics + label quality ก่อน ถ้าโอเคค่อยเลื่อน label_status → human-reviewed แล้ว freeze hash เป็นตัวจริงตอน Slice 2
