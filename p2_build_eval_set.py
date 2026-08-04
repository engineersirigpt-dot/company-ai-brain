"""
P2 synthetic eval-set generator rev2 (ปิด Codex eval-set B1-B6/M1-M2) — DRAFT/ai-reviewed
ยัง **ไม่ใช่ human-reviewed** — ต้องให้ Data Owner อ่าน+ลงชื่อจริงก่อนใช้ decision benchmark (B6)

ผลิต deterministic:
  p2_corpus.json    frozen corpus (synthetic marker + policy-v1) — shared distractor bank (IT_SYSTEMS,
                    ทุก role เห็น) ทำให้ authorized pool ต่อ role >= 60 (B1)
  p2_eval_set.json  ranking cases: intent_id + group-stratified split (B2), challenge_tags แยกจาก lang
                    (B3), hard_negative_ids, grade rubric+rationale (B5), source P2-SYNTH-* + synthetic=true (M2)

    PYTHONUTF8=1 python p2_build_eval_set.py
"""
import io
import json
import sys
import uuid

import policy as P
import p2_eval as E
from rbac_config import COLLECTIONS

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

GRADE_RATIONALE_3 = E.GRADE_RUBRIC[3]
GRADE_RATIONALE_2 = E.GRADE_RUBRIC[2]
CORPUS = {}


def pid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "kb-p2v2." + name))


def payload_for(collection):
    m = COLLECTIONS[collection]
    pol = P.DocumentPolicy(P.ACL_SCHEMA_VERSION, P.POLICY_VERSION, P.ACTIVE,
                           collection, m["confidentiality_level"], tuple(m["allowed_roles"]))
    ok, why = P.validate_document_policy(pol)
    assert ok, f"{collection}: {why}"
    d = pol.payload()
    d["synthetic"] = True                      # M2: กันเอาไปอ้างเป็นนโยบายจริง
    return d


def add_point(source, heading, text, collection):
    p = pid(f"{source}:{heading}")
    CORPUS[p] = {"source": source, "rerank_text": f"{heading} {text}", "payload": payload_for(collection)}
    return p


# ── B1: shared distractor bank (IT_SYSTEMS = ทุก role เห็น) → pool >= 60/role ────
def build_bank(n=64):
    topics = ["การรีเซ็ตรหัสผ่าน", "การตั้งค่าอีเมลบริษัท", "การจองห้องประชุม", "การเบิกอุปกรณ์สำนักงาน",
              "การขอ VPN", "การแจ้งซ่อมคอมพิวเตอร์", "การใช้เครื่องพิมพ์ส่วนกลาง", "การตั้งค่า WiFi",
              "การขอสิทธิ์เข้าโฟลเดอร์", "การอัปเดตโปรแกรม", "การสำรองไฟล์ส่วนตัว", "การใช้ระบบลางาน",
              "การบันทึกเวลาเข้าออก", "การขอนามบัตร", "การจองรถบริษัท", "การเบิกค่าเดินทาง"]
    ids = []
    for i in range(n):
        t = topics[i % len(topics)]
        ids.append(add_point(f"P2-SYNTH-BANK-{i:03d}", f"{t} (แนวปฏิบัติทั่วไป #{i})",
                             f"ขั้นตอน{t}ตามระเบียบภายใน ติดต่อ helpdesk หากมีปัญหา ({i})", "IT_SYSTEMS"))
    return ids


BANK = build_bank()


# ── families: แต่ละ topic → 1 intent (answer + hard-neg twin) ───────────────────
# spec = (topic_key, role, collection, answer_heading, answer_text, twin_heading, twin_text,
#         [ (query, lang, variant) ... ])
def gen_family(tag, specs, dev_first=0):
    """สร้าง intents (split=test ทั้งหมด) — dev ถูกเลือกทีหลังด้วย assign_splits ให้ครอบทุก role (B2.1)"""
    intents = []
    for s in specs:
        key, role, coll, ah, at, th, tt, paraphrases = s
        ans = add_point(f"P2-SYNTH-{key}", ah, at, coll)
        hard = [add_point(f"P2-SYNTH-{key}-TWIN", th, tt, coll)] if th else []
        intents.append({
            "intent_id": f"INT-{key}", "role": role, "tag": tag, "source": f"P2-SYNTH-{key}",
            "relevance": {ans: 3}, "hard_negative_ids": hard, "split": "test",
            "paraphrases": paraphrases,
        })
    return intents


EVALUATED_ROLES = ["sales", "qc", "hr", "purchasing", "production", "logistics", "engineering", "it"]


def assign_splits(intents, evaluated_roles, dev_per_role=2):
    """
    B2.1: เลือก dev_per_role intents ต่อ evaluated role -> dev (deterministic) ที่เหลือ test
    เลือกจาก 'direct' (non-gate) ก่อน เพื่อไม่ดึง gate-challenge test intents ออกจนต่ำกว่าเกณฑ์
    """
    by_role = {}
    for it in intents:
        by_role.setdefault(it["role"], []).append(it)
    for r in evaluated_roles:
        cand = sorted(by_role.get(r, []), key=lambda x: (x["tag"] != "direct", x["intent_id"]))
        for it in cand[:dev_per_role]:
            it["split"] = "dev"


# challenge families (แต่ละ family >= 6 test intents เพื่อ gate B3) --------------
FAMILIES = []

# DIRECT
FAMILIES += gen_family("direct", [
    ("D-QUOTE", "sales", "SALES", "การเสนอราคางานพิมพ์", "รับสเปกจากลูกค้าแล้วคิดราคาจาก cost sheet ก่อนออกใบเสนอราคา", "", "",
     [("ลูกค้าอยากได้ราคางานพิมพ์ ต้องเริ่มจากตรงไหน", "th", "colloquial"), ("quote งานพิมพ์เริ่มยังไง", "th-en", "mixed")]),
    ("D-RECALL", "qc", "RECALL", "การเรียกคืนผลิตภัณฑ์", "เมื่อพบของเสียถึงมือลูกค้าแล้ว แจ้ง QMR เปิด recall notice ระบุ lot", "", "",
     [("สินค้าเสียหลุดไปถึงลูกค้าต้องทำไง", "th", "colloquial"), ("product recall procedure", "th-en", "mixed")]),
    ("D-TRAIN", "hr", "HR", "การอบรมพนักงานใหม่", "พนักงานใหม่อบรม safety และ ISO ภายใน 30 วันแรก", "", "",
     [("เข้าใหม่ต้องอบรมอะไรบ้าง", "th", "colloquial"), ("new employee onboarding training", "th-en", "mixed")]),
    ("D-PO", "purchasing", "PURCHASING", "การจัดซื้อวัตถุดิบ", "เปิด PR เมื่อ stock ต่ำกว่า reorder point แล้วออก PO ให้ vendor ที่อนุมัติ", "", "",
     [("จะซื้อของเข้าคลังเริ่มยังไง", "th", "colloquial"), ("raw material purchasing flow", "th-en", "mixed")]),
    ("D-PRESS", "production", "PRODUCTION", "การตั้งเครื่องพิมพ์", "ตั้ง register และแรงกดตามใบสั่งงาน ทดพิมพ์ให้ QC อนุมัติ first article", "", "",
     [("ก่อนเดินเครื่องพิมพ์ต้องเซ็ตอะไร", "th", "colloquial"), ("press setup ก่อนรันจริง", "th-en", "mixed")]),
    ("D-SHIP", "logistics", "LOGISTICS", "การจัดส่งสินค้า", "เตรียม packing list ตรวจจำนวนก่อนขึ้นรถ ยืนยันปลายทาง", "", "",
     [("จะส่งของออกต้องเตรียมอะไร", "th", "colloquial"), ("shipping preparation steps", "th-en", "mixed")]),
    ("D-MAINT", "engineering", "ENGINEERING", "การบำรุงรักษาเครื่องจักร", "บำรุงรักษาเชิงป้องกันต้องตรวจ sensor และหล่อลื่นข้อต่อ", "", "",
     [("ดูแลเครื่องจักรต้องตรวจอะไรบ้าง", "th", "colloquial"), ("preventive maintenance ต้องตรวจอะไร", "th-en", "mixed")]),
    ("D-BACKUP", "it", "IT_SYSTEMS", "การสำรองฐานข้อมูล", "สำรองฐานข้อมูลทุกคืน เก็บ 30 วัน ทดสอบ restore ทุกไตรมาส", "", "",
     [("ข้อมูลระบบ backup ยังไง", "th", "colloquial"), ("database backup policy", "th-en", "mixed")]),
    ("D-COLOR2", "qc", "QUALITY", "การวัดสีก่อนส่งมอบ", "วัดค่าสีเทียบ proof ที่ลูกค้าอนุมัติก่อนปล่อยงาน", "", "",
     [("ก่อนส่งงานต้องเช็คสียังไง", "th", "colloquial"), ("color check ก่อน delivery", "th-en", "mixed")]),
    ("D-WRAP", "production", "PACKAGING", "การรัดฟิล์มพาเลท", "รัดฟิล์มพาเลทให้แน่นกันสินค้าล้มระหว่างขนย้าย", "", "",
     [("รัดพาเลทไม่ให้ของล้มทำไง", "th", "colloquial"), ("pallet wrapping กันล้ม", "th-en", "mixed")]),
    ("D-FIRSTLOGIN", "it", "IT_SYSTEMS", "การเข้าระบบครั้งแรก", "เข้าครั้งแรกใช้รหัสชั่วคราวแล้วเปลี่ยนทันที", "", "",
     [("พนักงานใหม่ล็อกอินระบบครั้งแรกยังไง", "th", "colloquial"), ("first time login ทำไง", "th-en", "mixed")]),
    ("D-PAYDAY", "hr", "HR", "รอบการจ่ายเงินเดือน", "จ่ายเงินเดือนทุกวันที่ 28 ของเดือน", "", "",
     [("เงินเดือนออกวันไหน", "th", "colloquial"), ("payday วันที่เท่าไร", "th-en", "mixed")]),
    ("D-SORT", "qc", "QUALITY", "การคัดแยกของเสีย", "แยกงานของเสียออกจากงานดีทันทีและติดป้ายกันปน", "", "",
     [("เจอของเสียแล้วต้องแยกยังไง", "th", "colloquial"), ("defect sorting ทำไง", "th-en", "mixed")]),
    ("D-EXPORTDOC", "logistics", "LOGISTICS", "เอกสารสำหรับส่งออก", "ส่งออกต้องมี invoice และ packing list ครบก่อนออกของ", "", "",
     [("ส่งของออกนอกต้องใช้เอกสารอะไร", "th", "colloquial"), ("export documents ต้องมีอะไร", "th-en", "mixed")]),
    ("D-READSPEC", "engineering", "ENGINEERING", "การอ่านแบบเครื่องจักร", "อ่านแบบตามสัญลักษณ์มาตรฐานและตรวจ tolerance ตามที่กำหนด", "", "",
     [("อ่านแบบเครื่องจักรดูตรงไหน", "th", "colloquial"), ("machine drawing อ่านยังไง", "th-en", "mixed")]),
    ("D-MAKEREADY", "production", "PRODUCTION", "การเตรียม make-ready", "เตรียมเพลท หมึก กระดาษให้ตรง job ด้วย checklist ลดเวลา setup", "", "",
     [("เตรียมงานก่อนพิมพ์ลดเวลายังไง", "th", "colloquial"), ("make-ready ลด setup time", "th-en", "mixed")]),
    ("D-FOLLOWUP", "sales", "SALES", "การติดตามลูกค้า", "ติดตามลูกค้าภายใน 7 วันหลังส่งใบเสนอราคาและบันทึกใน CRM", "", "",
     [("ส่งใบเสนอราคาไปแล้วตามลูกค้ายังไง", "th", "colloquial"), ("customer follow-up หลัง quote", "th-en", "mixed")]),
    ("D-VPN", "it", "IT_SYSTEMS", "การขอใช้ VPN", "ขอ VPN ผ่านระบบและต้องได้รับอนุมัติหัวหน้าก่อนใช้งานนอกสถานที่", "", "",
     [("จะใช้ VPN ทำงานนอกออฟฟิศต้องทำไง", "th", "colloquial"), ("request VPN access ยังไง", "th-en", "mixed")]),
    ("D-PWRESET", "it", "IT_SYSTEMS", "การรีเซ็ตรหัสผ่านที่ลืม", "แจ้ง helpdesk ยืนยันตัวตนแล้วรับรหัสชั่วคราวเปลี่ยนทันที", "", "",
     [("ลืมรหัสผ่านเข้าระบบทำยังไง", "th", "colloquial"), ("forgot password reset ทำไง", "th-en", "mixed")]),
    ("D-INSPECT", "engineering", "ENGINEERING", "การตรวจสภาพเครื่องประจำวัน", "ตรวจเสียงผิดปกติ อุณหภูมิ และการรั่วซึมก่อนเริ่มงานทุกวัน", "", "",
     [("ก่อนเริ่มงานต้องเช็คเครื่องอะไรบ้าง", "th", "colloquial"), ("daily machine check ดูอะไร", "th-en", "mixed")]),
    ("D-CONTRACT", "sales", "SALES", "การจัดทำสัญญาขาย", "ออกสัญญาหลังลูกค้ายืนยันใบเสนอราคาและตรวจเงื่อนไขการชำระเงิน", "", "",
     [("ลูกค้าตกลงแล้วทำสัญญายังไง", "th", "colloquial"), ("sales contract หลังลูกค้า confirm", "th-en", "mixed")]),
    ("D-STOCK", "purchasing", "PURCHASING", "การตรวจนับสต็อกวัตถุดิบ", "ตรวจนับสต็อกวัตถุดิบทุกสิ้นเดือนเทียบยอดในระบบ", "", "",
     [("นับสต็อกวัตถุดิบทำเมื่อไร", "th", "colloquial"), ("stock count วัตถุดิบทำตอนไหน", "th-en", "mixed")]),
    ("D-QCFIRST", "qc", "QUALITY", "การตรวจ first article", "ตรวจชิ้นแรกของงานเทียบสเปกก่อนอนุมัติให้ผลิตต่อ", "", "",
     [("ตรวจชิ้นแรกก่อนผลิตยังไง", "th", "colloquial"), ("first article inspection ทำไง", "th-en", "mixed")]),
    ("D-HRRECORD", "hr", "HR", "การเก็บประวัติพนักงาน", "เก็บเอกสารประวัติและสัญญาจ้างในแฟ้มบุคคลตามระเบียบ", "", "",
     [("เก็บเอกสารประวัติพนักงานยังไง", "th", "colloquial"), ("employee record filing ทำไง", "th-en", "mixed")]),
])

# SIBLING-HARD-NEGATIVE (answer + sibling ในเอกสารเดียวที่ใกล้แต่ผิด)
FAMILIES += gen_family("sibling-hard-negative", [
    ("S-APPROVE", "sales", "SALES", "การอนุมัติใบเสนอราคา", "ใบเสนอราคาเกิน 500,000 บาทต้องผ่านผู้จัดการฝ่ายขาย",
     "การส่งใบเสนอราคา", "ส่งใบเสนอราคาทางอีเมลพร้อมสำเนาถึงฝ่ายบัญชี",
     [("ใบเสนอราคาต้องให้ใครอนุมัติ", "th", "direct"), ("quotation approval limit เท่าไร", "th-en", "mixed")]),
    ("S-MOCK", "qc", "RECALL", "การซ้อม mock recall", "ซ้อม mock recall ปีละ 1 ครั้ง วัดเวลาไม่เกิน 4 ชั่วโมง",
     "การรายงานผลเรียกคืน", "สรุปยอดเรียกคืนต่อผู้บริหารภายใน 3 วัน",
     [("ต้องซ้อมเรียกคืนบ่อยแค่ไหน", "th", "direct"), ("mock recall frequency", "th-en", "mixed")]),
    ("S-LEAVE", "hr", "HR", "ระเบียบการลาป่วย", "ลาป่วยเกิน 3 วันต้องมีใบรับรองแพทย์",
     "ระเบียบการลากิจ", "ลากิจต้องแจ้งล่วงหน้าอย่างน้อย 1 วัน",
     [("ลาป่วยกี่วันต้องมีใบแพทย์", "th", "direct"), ("sick leave ต้องมี medical cert เมื่อไร", "th-en", "mixed")]),
    ("S-VENDOR", "purchasing", "PURCHASING", "การประเมิน vendor", "ประเมิน vendor ทุก 6 เดือน ด้านคุณภาพและการส่งมอบ",
     "การขึ้นทะเบียน vendor ใหม่", "vendor ใหม่ต้องส่งเอกสารรับรองก่อนอนุมัติ",
     [("ประเมินซัพพลายเออร์ทุกกี่เดือน", "th", "direct"), ("vendor evaluation cycle", "th-en", "mixed")]),
    ("S-WASTE", "production", "PRODUCTION", "การควบคุมของเสียการผลิต", "บันทึก waste ต่อ job เทียบเป้าไม่เกิน 5%",
     "การควบคุมความเร็วเครื่อง", "ตั้งความเร็วเครื่องตามชนิดกระดาษ",
     [("เป้าของเสียการผลิตไม่เกินเท่าไร", "th", "direct"), ("production waste target %", "th-en", "mixed")]),
    ("S-PLAN", "logistics", "LOGISTICS", "การวางแผนขนส่ง", "รวม order ปลายทางเดียวกันเพื่อลดเที่ยวรถ",
     "การเลือกผู้ขนส่ง", "เลือกผู้ขนส่งจากราคาและความตรงต่อเวลา",
     [("ลดค่าขนส่งด้วยการวางแผนยังไง", "th", "direct"), ("transport planning ลดเที่ยว", "th-en", "mixed")]),
    ("S-CAL", "engineering", "ENGINEERING", "การหล่อลื่นข้อต่อ", "หล่อลื่นข้อต่อแขนกลทุกสัปดาห์ตามคู่มือ",
     "การตรวจ sensor", "ตรวจความไวของ sensor ทุกเดือน",
     [("ต้องหล่อลื่นข้อต่อบ่อยแค่ไหน", "th", "direct"), ("joint lubrication interval", "th-en", "mixed")]),
], dev_first=1)

# TABLE-ROW (chunk เป็นตารางจริง — row: param | value ; ถามค่าของ row เจาะจง)
def _tbl(rows):
    return "ตาราง: " + " ; ".join(f"{k} = {v}" for k, v in rows)
FAMILIES += gen_family("table-row", [
    ("T-DE", "qc", "QUALITY", "เกณฑ์ค่าสีงานพิมพ์ (ตาราง)", _tbl([("Delta-E สูงสุด", "3.0"), ("ค่าความเงา", "70 GU"), ("ความหนากระดาษ", "157 gsm")]),
     "เกณฑ์ AQL งานพิมพ์", _tbl([("AQL major", "1.0"), ("AQL minor", "2.5")]),
     [("ค่า Delta-E สูงสุดที่ยอมรับได้เท่าไร", "th", "row-lookup"), ("max Delta-E จากตารางเท่าไร", "th-en", "mixed")]),
    ("T-LEADTIME", "sales", "SALES", "ตาราง lead time งานพิมพ์", _tbl([("นามบัตร", "3 วัน"), ("โบรชัวร์", "7 วัน"), ("กล่อง", "10 วัน")]),
     "ตารางส่วนลดตามยอด", _tbl([("ยอด 1 แสน", "3%"), ("ยอด 5 แสน", "5%")]),
     [("โบรชัวร์ lead time กี่วัน", "th", "row-lookup"), ("brochure lead time จากตาราง", "th-en", "mixed")]),
    ("T-REORDER", "purchasing", "PURCHASING", "ตาราง reorder point วัตถุดิบ", _tbl([("กระดาษอาร์ต", "500 รีม"), ("หมึกดำ", "50 กก."), ("กาว", "20 ลิตร")]),
     "ตารางราคาต่อหน่วย", _tbl([("กระดาษอาร์ต", "120 บาท/รีม")]),
     [("หมึกดำ reorder point เท่าไร", "th", "row-lookup"), ("black ink reorder จากตาราง", "th-en", "mixed")]),
    ("T-SPEED", "production", "PRODUCTION", "ตารางความเร็วเครื่องพิมพ์", _tbl([("กระดาษบาง", "8000 แผ่น/ชม."), ("กระดาษหนา", "5000 แผ่น/ชม.")]),
     "ตารางแรงกด", _tbl([("กระดาษบาง", "ระดับ 2")]),
     [("กระดาษหนาเดินเครื่องได้กี่แผ่นต่อชั่วโมง", "th", "row-lookup"), ("thick paper speed จากตาราง", "th-en", "mixed")]),
    ("T-PALLET", "logistics", "LOGISTICS", "ตารางน้ำหนักพาเลท", _tbl([("พาเลทไม้", "25 กก."), ("พาเลทพลาสติก", "15 กก.")]),
     "ตารางขนาดกล่อง", _tbl([("กล่อง A", "30x20x15 ซม.")]),
     [("พาเลทพลาสติกหนักเท่าไร", "th", "row-lookup"), ("plastic pallet weight จากตาราง", "th-en", "mixed")]),
    ("T-TORQUE", "engineering", "ENGINEERING", "ตารางค่า torque แขนกล", _tbl([("ข้อต่อฐาน", "40 Nm"), ("ข้อต่อกลาง", "25 Nm"), ("ข้อต่อปลาย", "12 Nm")]),
     "ตารางระยะเคลื่อนที่", _tbl([("แกน X", "800 มม.")]),
     [("torque ข้อต่อกลางเท่าไร", "th", "row-lookup"), ("middle joint torque จากตาราง", "th-en", "mixed")]),
], dev_first=1)

# NEGATION (chunk มี rule บวก+ลบ ชัด ; query ทดสอบนัยเชิงลบให้ตรง)
FAMILIES += gen_family("negation", [
    ("N-DEFECT", "qc", "QUALITY", "เกณฑ์ผ่าน/ไม่ผ่านงานพิมพ์", "งานผ่านเมื่อผ่านทุกเกณฑ์ทั้งสีและความสะอาด งานที่ไม่มีจุดสกปรกแต่สีเพี้ยนเกิน Delta-E 3.0 ถือว่าไม่ผ่าน",
     "", "",
     [("งานไม่มีจุดสกปรกแต่สีเพี้ยน ถือว่าผ่านไหม", "th", "negation"), ("clean but off-color ผ่านไหม", "th-en", "mixed")]),
    ("N-RECALL", "qc", "RECALL", "ขอบเขตการเปิด recall", "เปิด recall เฉพาะเมื่อสินค้าถึงมือลูกค้าแล้ว หากยังอยู่ในคลังให้ใช้ hold ภายในไม่ใช่ recall",
     "", "",
     [("สินค้ายังไม่ส่งลูกค้า ต้องเปิด recall ไหม", "th", "negation"), ("still in warehouse ใช้ recall ไหม", "th-en", "mixed")]),
    ("N-VENDOR", "purchasing", "PURCHASING", "การใช้ vendor สำรอง", "ใช้ vendor สำรองได้เฉพาะกรณีเร่งด่วน งานปกติห้ามใช้ vendor สำรอง",
     "", "",
     [("งานจัดซื้อปกติใช้ vendor สำรองได้ไหม", "th", "negation"), ("normal order ใช้ backup vendor ได้ไหม", "th-en", "mixed")]),
    ("N-OT", "hr", "HR", "เงื่อนไขการทำ OT", "OT ต้องได้รับอนุมัติล่วงหน้า OT ที่ไม่ได้อนุมัติล่วงหน้าเบิกไม่ได้",
     "", "",
     [("ทำ OT โดยไม่ขออนุมัติก่อน เบิกได้ไหม", "th", "negation"), ("OT ไม่ได้ approve เบิกได้ไหม", "th-en", "mixed")]),
    ("N-FSC", "qc", "QUALITY", "การใช้สัญลักษณ์ FSC", "ใช้ FSC ได้เฉพาะงานที่ certified งานที่ไม่ได้ certified ห้ามพิมพ์ FSC แม้ลูกค้าขอ",
     "", "",
     [("ลูกค้าขอ FSC แต่งานไม่ certified พิมพ์ได้ไหม", "th", "negation"), ("non-certified job ใส่ FSC ได้ไหม", "th-en", "mixed")]),
    ("N-SHIP", "logistics", "LOGISTICS", "การปล่อยสินค้า", "ปล่อยสินค้าได้เมื่อ QC ผ่านเท่านั้น สินค้าที่ QC ยังไม่ผ่านห้ามขึ้นรถ",
     "", "",
     [("QC ยังไม่ผ่าน ขึ้นรถส่งก่อนได้ไหม", "th", "negation"), ("QC not passed ส่งก่อนได้ไหม", "th-en", "mixed")]),
], dev_first=1)

# CURRENT-SUPERSEDED (คู่ revision เก่า/ใหม่ ; ถาม current, hard-neg = superseded)
FAMILIES += gen_family("current-superseded", [
    ("C-DE", "qc", "QUALITY", "เกณฑ์ Delta-E ฉบับปัจจุบัน (rev.3, 2026)", "ฉบับปัจจุบัน rev.3 กำหนด Delta-E สูงสุด 3.0",
     "เกณฑ์ Delta-E ฉบับเก่า (rev.2, ยกเลิกแล้ว)", "ฉบับเก่า rev.2 (superseded) เคยกำหนด Delta-E 5.0 ปัจจุบันยกเลิกแล้ว",
     [("เกณฑ์ Delta-E ที่ใช้อยู่ตอนนี้เท่าไร", "th", "current"), ("current Delta-E limit เท่าไร", "th-en", "mixed")]),
    ("C-LEAD", "sales", "SALES", "lead time นามบัตรฉบับปัจจุบัน (2026)", "ฉบับปัจจุบันกำหนดนามบัตร lead time 3 วัน",
     "lead time นามบัตรฉบับเก่า (ยกเลิก)", "ฉบับเก่า (superseded) เคยกำหนด 5 วัน ยกเลิกแล้ว",
     [("lead time นามบัตรที่ใช้ปัจจุบันกี่วัน", "th", "current"), ("current business card lead time", "th-en", "mixed")]),
    ("C-REORDER", "purchasing", "PURCHASING", "reorder หมึกดำฉบับปัจจุบัน", "ฉบับปัจจุบัน reorder หมึกดำที่ 50 กก.",
     "reorder หมึกดำฉบับเก่า (ยกเลิก)", "ฉบับเก่า (superseded) เคยตั้ง 30 กก. ยกเลิกแล้ว",
     [("reorder หมึกดำปัจจุบันเท่าไร", "th", "current"), ("current black ink reorder", "th-en", "mixed")]),
    ("C-OT", "hr", "HR", "อัตรา OT ฉบับปัจจุบัน", "ฉบับปัจจุบันจ่าย OT 1.5 เท่าของค่าแรงปกติ",
     "อัตรา OT ฉบับเก่า (ยกเลิก)", "ฉบับเก่า (superseded) เคยจ่าย 1.25 เท่า ยกเลิกแล้ว",
     [("อัตรา OT ที่ใช้ตอนนี้กี่เท่า", "th", "current"), ("current OT rate", "th-en", "mixed")]),
    ("C-SPEED", "production", "PRODUCTION", "ความเร็วกระดาษหนาฉบับปัจจุบัน", "ฉบับปัจจุบันกำหนดกระดาษหนา 5000 แผ่น/ชม.",
     "ความเร็วกระดาษหนาฉบับเก่า (ยกเลิก)", "ฉบับเก่า (superseded) เคยตั้ง 6000 แผ่น/ชม. ยกเลิกแล้ว",
     [("ความเร็วกระดาษหนาที่ใช้ปัจจุบันเท่าไร", "th", "current"), ("current thick paper speed", "th-en", "mixed")]),
    ("C-MAINT", "engineering", "ENGINEERING", "รอบบำรุงรักษาฉบับปัจจุบัน", "ฉบับปัจจุบันบำรุงรักษาทุก 1 สัปดาห์",
     "รอบบำรุงรักษาฉบับเก่า (ยกเลิก)", "ฉบับเก่า (superseded) เคยกำหนดทุก 2 สัปดาห์ ยกเลิกแล้ว",
     [("รอบบำรุงรักษาที่ใช้ปัจจุบันเท่าไร", "th", "current"), ("current maintenance interval", "th-en", "mixed")]),
], dev_first=1)

# LEXICAL-OVERLAP-WRONG-CODE (คำซ้ำแต่คนละ code/เอกสาร)
FAMILIES += gen_family("lexical-overlap", [
    ("L-721", "sales", "SALES", "การเสนอราคางาน P2-SYNTH-721", "P2-SYNTH-721 ว่าด้วยการคิดราคาและออกใบเสนอราคา",
     "การจัดทำ JOB packaging P2-SYNTH-722", "P2-SYNTH-722 ว่าด้วยการจัดทำใบ JOB สำหรับงานบรรจุ ไม่ใช่การเสนอราคา",
     [("ขั้นตอนเสนอราคาตาม P2-SYNTH-721", "th", "code"), ("P2-SYNTH-721 quotation steps", "th-en", "mixed")]),
    ("L-QP741", "purchasing", "PURCHASING", "การจัดซื้อ P2-SYNTH-741", "P2-SYNTH-741 ว่าด้วยการเปิด PR และออก PO",
     "การรับเข้าวัตถุดิบ P2-SYNTH-742", "P2-SYNTH-742 ว่าด้วยการตรวจรับวัตถุดิบ ไม่ใช่การจัดซื้อ",
     [("การจัดซื้อตาม P2-SYNTH-741 ทำยังไง", "th", "code"), ("P2-SYNTH-741 purchasing flow", "th-en", "mixed")]),
    ("L-WI423", "qc", "QUALITY", "การควบคุม FSC P2-SYNTH-423", "P2-SYNTH-423 ว่าด้วยการตรวจสัญลักษณ์ FSC",
     "การสอบเทียบเครื่องมือ P2-SYNTH-424", "P2-SYNTH-424 ว่าด้วยการสอบเทียบเครื่องวัด ไม่ใช่ FSC",
     [("การควบคุม FSC ตาม P2-SYNTH-423", "th", "code"), ("P2-SYNTH-423 FSC control", "th-en", "mixed")]),
    ("L-QP710", "production", "PRODUCTION", "การตั้งเครื่อง P2-SYNTH-710", "P2-SYNTH-710 ว่าด้วยการตั้ง register และแรงกด",
     "การล้างเครื่อง P2-SYNTH-711", "P2-SYNTH-711 ว่าด้วยการล้างลูกกลิ้ง ไม่ใช่การตั้งเครื่อง",
     [("การตั้งเครื่องตาม P2-SYNTH-710", "th", "code"), ("P2-SYNTH-710 press setup", "th-en", "mixed")]),
    ("L-WI755", "logistics", "LOGISTICS", "การจัดส่ง P2-SYNTH-755", "P2-SYNTH-755 ว่าด้วยการเตรียมเอกสารและตรวจจำนวนก่อนส่ง",
     "การรับคืนสินค้า P2-SYNTH-756", "P2-SYNTH-756 ว่าด้วยการรับคืนสินค้า ไม่ใช่การจัดส่ง",
     [("การจัดส่งตาม P2-SYNTH-755", "th", "code"), ("P2-SYNTH-755 shipping steps", "th-en", "mixed")]),
    ("L-QP852", "qc", "RECALL", "การเรียกคืน P2-SYNTH-852", "P2-SYNTH-852 ว่าด้วยการเปิด recall notice",
     "การกักกันภายใน P2-SYNTH-853", "P2-SYNTH-853 ว่าด้วยการ hold ในคลัง ไม่ใช่การเรียกคืน",
     [("การเรียกคืนตาม P2-SYNTH-852", "th", "code"), ("P2-SYNTH-852 recall steps", "th-en", "mixed")]),
], dev_first=1)

# MULTI-CONSTRAINT (2 เงื่อนไข ; answer ตอบครบ, hard-neg ตอบครึ่งเดียว)
FAMILIES += gen_family("multi-constraint", [
    ("M-QUOTE", "sales", "SALES", "การเสนอราคางานเร่งด่วนเกิน 5 แสน", "งานเร่งด่วนและเกิน 500,000 บาท ต้องคิด surcharge 10% และผ่านผู้จัดการ",
     "การเสนอราคางานปกติ", "งานปกติคิดราคาตาม cost sheet ไม่มี surcharge",
     [("งานเร่งด่วนและเกินห้าแสนคิดราคายังไง", "th", "multi"), ("urgent และ over 500k pricing", "th-en", "mixed")]),
    ("M-SHIP", "logistics", "LOGISTICS", "การส่งออกต่างประเทศแบบด่วน", "ส่งออกต่างประเทศและเร่งด่วนต้องมีเอกสารศุลกากรครบและใช้ผู้ขนส่งด่วนที่อนุมัติ",
     "การส่งในประเทศ", "ส่งในประเทศใช้เอกสาร packing list ปกติ",
     [("ส่งออกนอกแบบด่วนต้องมีอะไรบ้าง", "th", "multi"), ("urgent export requirement", "th-en", "mixed")]),
    ("M-PO", "purchasing", "PURCHASING", "การจัดซื้อเร่งด่วนมูลค่าสูง", "จัดซื้อเร่งด่วนและมูลค่าเกิน 200,000 ใช้ vendor สำรองได้แต่ต้องขออนุมัติผู้จัดการย้อนหลัง 24 ชม.",
     "การจัดซื้อมูลค่าต่ำ", "มูลค่าต่ำเปิด PO ปกติ",
     [("ซื้อด่วนและมูลค่าสูงทำยังไง", "th", "multi"), ("urgent high-value purchase", "th-en", "mixed")]),
    ("M-QC", "qc", "QUALITY", "การตรวจงานสีพิเศษจำนวนมาก", "งานสีพิเศษและจำนวนเกิน 10,000 ชิ้น ต้องสุ่มตรวจถี่ขึ้นและวัด Delta-E ทุกล็อต",
     "การตรวจงานทั่วไป", "งานทั่วไปสุ่มตรวจตาม AQL ปกติ",
     [("งานสีพิเศษจำนวนมากตรวจยังไง", "th", "multi"), ("special color large batch inspection", "th-en", "mixed")]),
    ("M-PROD", "production", "PRODUCTION", "การผลิตกระดาษหนางานด่วน", "งานด่วนและกระดาษหนา ต้องลดความเร็วเหลือ 4000 แผ่น/ชม. และเพิ่มคนคุมเครื่อง",
     "การผลิตงานปกติ", "งานปกติเดินตามความเร็วมาตรฐาน",
     [("งานด่วนกระดาษหนาต้องปรับอะไร", "th", "multi"), ("urgent thick paper production", "th-en", "mixed")]),
    ("M-HR", "hr", "HR", "การอบรมพนักงานใหม่ตำแหน่งคุมเครื่อง", "พนักงานใหม่และเป็นตำแหน่งคุมเครื่อง ต้องอบรม safety เพิ่มและผ่านทดสอบก่อนขึ้นเครื่อง",
     "การอบรมพนักงานใหม่ทั่วไป", "พนักงานใหม่ทั่วไปอบรม safety และ ISO พื้นฐาน",
     [("พนักงานใหม่ตำแหน่งคุมเครื่องอบรมต่างจากทั่วไปยังไง", "th", "multi"), ("new machine operator training", "th-en", "mixed")]),
], dev_first=1)

# GRADED-MULTI (primary grade 3 = ตอบทั้งสองส่วนของ conjunction จริง ; supporting grade 2 = เสริม)
# spec = (key, role, coll, (prim_h, prim_t), (supp_h, supp_t), rat_primary, rat_support, paraphrases)
GRADED = [
    ("G-QUOTE", "sales", "SALES",
     ("การเสนอราคาและการอนุมัติใบเสนอราคา", "รับสเปกลูกค้าแล้วคิดราคาจาก cost sheet ออกใบเสนอราคา และงานเกิน 500,000 บาทต้องส่งผู้จัดการฝ่ายขายอนุมัติก่อนส่งลูกค้า"),
     ("การติดตามหลังส่งใบเสนอราคา", "ติดตามลูกค้าภายใน 7 วันและบันทึกผลใน CRM"),
     "ตอบทั้งขั้นตอนเสนอราคาและเงื่อนไขการอนุมัติซึ่งเป็นแกนของคำถาม",
     "เสริมขั้นตอนติดตามหลังเสนอราคา ไม่ใช่แกนคำถามโดยตรง",
     [("การเสนอราคาและการอนุมัติทำอย่างไร", "th", "conjunction"), ("quotation and approval process", "th-en", "mixed")]),
    ("G-RECALL", "qc", "RECALL",
     ("การเรียกคืนและการรายงานผล", "แจ้ง QMR เปิด recall notice ระบุ lot ที่กระทบ ติดตามสินค้าคืน แล้วสรุปยอดที่เรียกคืนได้รายงานต่อผู้บริหารภายใน 3 วัน"),
     ("การซ้อม mock recall", "ซ้อม mock recall ปีละ 1 ครั้งเพื่อวัดเวลาติดตามคืน"),
     "ตอบทั้งการเปิดเรียกคืนและการรายงานผลตามคำถาม",
     "เสริมเรื่องการซ้อม ไม่ใช่การเรียกคืน/รายงานจริง",
     [("การเรียกคืนและการรายงานผลทำอย่างไร", "th", "conjunction"), ("recall and result reporting", "th-en", "mixed")]),
    ("G-PO", "purchasing", "PURCHASING",
     ("การจัดซื้อและการประเมิน vendor", "เปิด PR เมื่อ stock ต่ำกว่า reorder ออก PO ให้ vendor ที่อนุมัติ และประเมิน vendor ทุก 6 เดือนด้านคุณภาพและการส่งมอบ"),
     ("การขึ้นทะเบียน vendor ใหม่", "vendor ใหม่ต้องส่งเอกสารรับรองก่อนอนุมัติเข้าทะเบียน"),
     "ตอบทั้งการจัดซื้อและรอบการประเมิน vendor ตามคำถาม",
     "เสริมการขึ้นทะเบียน vendor ใหม่ ไม่ใช่แกนคำถาม",
     [("การจัดซื้อและการประเมิน vendor ทำอย่างไร", "th", "conjunction"), ("purchasing and vendor evaluation", "th-en", "mixed")]),
    ("G-QC", "qc", "QUALITY",
     ("การตรวจสีและการตัดสินของเสีย", "วัดค่าสีเทียบ proof ที่อนุมัติโดย Delta-E ต้องไม่เกิน 3.0 และงานที่เกินเกณฑ์สีหรือมีจุดสกปรกเกิน AQL ถือเป็นของเสีย"),
     ("การบันทึกผลตรวจ", "บันทึกผลวัดสีและจำนวนของเสียต่อ job"),
     "ตอบทั้งวิธีวัดสีและเกณฑ์ตัดสินของเสียตามคำถาม",
     "เสริมการบันทึกผล ไม่ใช่เกณฑ์ตัดสิน",
     [("การตรวจสีและการตัดสินของเสียทำอย่างไร", "th", "conjunction"), ("color check and defect judgement", "th-en", "mixed")]),
    ("G-SHIP", "logistics", "LOGISTICS",
     ("การจัดส่งและการวางแผนขนส่ง", "เตรียม packing list ตรวจจำนวนก่อนขึ้นรถและยืนยันปลายทาง โดยวางแผนรวม order ปลายทางเดียวกันเพื่อลดจำนวนเที่ยวรถ"),
     ("การเลือกผู้ขนส่ง", "เลือกผู้ขนส่งจากราคาและความตรงต่อเวลา"),
     "ตอบทั้งขั้นตอนจัดส่งและวิธีวางแผนลดเที่ยวตามคำถาม",
     "เสริมการเลือกผู้ขนส่ง ไม่ใช่แกนคำถาม",
     [("การจัดส่งและการวางแผนขนส่งทำอย่างไร", "th", "conjunction"), ("shipping and transport planning", "th-en", "mixed")]),
]
for key, role, coll, prim, supp, rat_p, rat_s, paraphrases in GRADED:
    p3 = add_point(f"P2-SYNTH-{key}", prim[0], prim[1], coll)
    p2 = add_point(f"P2-SYNTH-{key}-SUP", supp[0], supp[1], coll)
    FAMILIES.append({
        "intent_id": f"INT-{key}", "role": role, "tag": "graded-multi", "source": f"P2-SYNTH-{key}",
        "relevance": {p3: 3, p2: 2}, "hard_negative_ids": [],
        "grade_rationale": {p3: rat_p, p2: rat_s},
        "split": "test", "paraphrases": paraphrases,
    })

assign_splits(FAMILIES, EVALUATED_ROLES, dev_per_role=2)   # B2.1: dev ครอบทุก evaluated role


def build_cases():
    cases, n = [], 0
    for it in FAMILIES:
        rel = it["relevance"]
        src = sorted({CORPUS[p]["source"] for p in rel})
        for q, lang, variant in it["paraphrases"]:
            c = {"query_id": f"q-{n:04d}", "intent_id": it["intent_id"], "query": q, "role": it["role"],
                 "lang": lang, "category": it["tag"], "challenge_tags": [it["tag"]], "split": it["split"],
                 "case_type": "ranking", "relevance": rel, "hard_negative_ids": it["hard_negative_ids"],
                 "relevant_sources": src, "label_status": "ai-reviewed",
                 "reviewed_by": "claude-ai (generator draft)", "review_revision": "rev2",
                 "variant": variant}
            if "grade_rationale" in it:
                c["grade_rationale"] = it["grade_rationale"]
            cases.append(c)
            n += 1
    return cases


GATE_TAGS = ["sibling-hard-negative", "table-row", "negation", "current-superseded",
             "lexical-overlap", "multi-constraint"]


def role_pool_sizes():
    sizes = {}
    for r in sorted(P.KNOWN_ROLES):
        acc = P.EffectiveAccess(P.ServicePrincipal("t", (r,), True, "enforce"), r)
        spec = P.compile_retrieval_filter(acc)
        sizes[r] = sum(1 for e in CORPUS.values() if P.matches_policy(e["payload"], spec))
    return sizes


def main():
    cases = build_cases()
    with open("p2_corpus.json", "w", encoding="utf-8") as f:
        json.dump(CORPUS, f, ensure_ascii=False, indent=1)
    with open("p2_eval_set.json", "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)

    known = set(P.KNOWN_ROLES)
    struct = E.validate_ranking_eval_set(cases, CORPUS, known)
    only_label = [e for e in struct if "label_status" not in e]
    arm = E.arm_eligibility_errors(cases, GATE_TAGS)
    devcov = E.dev_role_coverage_errors(cases, EVALUATED_ROLES, min_dev_per_role=1)
    decision = E.decision_benchmark_errors(cases, CORPUS, known, EVALUATED_ROLES, GATE_TAGS, signoff=None)
    dev = [c for c in cases if c["split"] == "dev"]
    test = [c for c in cases if c["split"] == "test"]
    pools = role_pool_sizes()
    dev_by_role = {r: len({c["intent_id"] for c in dev if c["role"] == r}) for r in EVALUATED_ROLES}

    print(f"corpus points     : {len(CORPUS)} (bank {len(BANK)} + answers/twins)")
    print(f"cases             : {len(cases)} (dev {len(dev)}, test {len(test)})")
    print(f"intents           : dev {len({c['intent_id'] for c in dev})}, test {E.count_test_intents(cases)} (ต้อง >= {E.MIN_TEST_INTENTS})")
    print(f"corpus valid      : {E.validate_corpus(CORPUS) == []}")
    print(f"struct errors (ยกเว้น label_status ai-reviewed): {len(only_label)}")
    for e in only_label[:8]:
        print(f"  - {e}")
    print(f"arm_eligibility   : {'PASS' if not arm else 'FAIL'} {arm}")
    print(f"dev_role_coverage : {'PASS' if not devcov else 'FAIL'} {devcov}")
    print(f"  dev intents/role: " + ", ".join(f"{r}={dev_by_role[r]}" for r in EVALUATED_ROLES))
    print(f"pool ต่อ evaluated role (ต้อง >= 60): " + ", ".join(f"{r}={pools[r]}" for r in EVALUATED_ROLES))
    print(f"label_status      : ai-reviewed (B6: รอ Data Owner ลงชื่อ human-reviewed)")
    print(f"decision_benchmark: BLOCKED (ตามคาด) — เหลือ {len(decision)} ข้อ (label human-reviewed + Data Owner sign-off)")
    print(f"eval hash {E.eval_set_sha256(cases)[:16]} · corpus hash {E.corpus_manifest_sha256(CORPUS)[:16]}")


if __name__ == "__main__":
    main()
