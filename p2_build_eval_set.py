"""
P2 synthetic eval-set generator (DRAFT — non-gated prep ระหว่างรอ Codex confirm Slice 2)

ผลิต deterministic:
  - p2_corpus.json     : frozen corpus manifest {point_id: {source, rerank_text, payload(policy-v1)}}
  - p2_eval_set.json   : ranking cases (dev/test split, graded relevance by-construction)

by-construction relevance: เขียน query + chunk คู่กัน chunk ที่ตอบตรงคือ relevant (grade 3);
sibling/lexical-overlap ที่ผิดคือ hard negative (อยู่ใน corpus แต่ไม่อยู่ใน relevance)

label_status = "draft" — ต้องให้คน/Codex review ก่อนเลื่อนเป็น "human-reviewed" (benchmark gate)
ทุก case ผ่าน validate_ranking_eval_set ยกเว้น label_status (ตั้งใจ) — generator รายงานให้เห็น

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


def pid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "kb-p2." + name))


def payload_for(collection):
    meta = COLLECTIONS[collection]
    pol = P.DocumentPolicy(P.ACL_SCHEMA_VERSION, P.POLICY_VERSION, P.ACTIVE,
                           collection, meta["confidentiality_level"], tuple(meta["allowed_roles"]))
    ok, reason = P.validate_document_policy(pol)
    assert ok, f"{collection} payload invalid: {reason}"
    return pol.payload()


# ── DOCUMENTS: (doc_id, collection, primary_role, [chunks]) ────────────────────
# chunk = (key, heading, text). chunk[0] = canonical answer ของ query ตรง; ที่เหลือ = sibling
DOCS = [
    ("SOP-SALES-721", "SALES", "sales", [
        ("quote-steps", "ขั้นตอนการเสนอราคางานพิมพ์ (WI-721)",
         "รับ enquiry จากลูกค้า ตรวจสเปกงานพิมพ์ จำนวน ขนาด กระดาษ แล้วคำนวณราคาตาม cost sheet ก่อนออกใบเสนอราคา"),
        ("approve", "การอนุมัติใบเสนอราคา", "ใบเสนอราคาเกิน 500,000 บาท ต้องผ่านการอนุมัติจากผู้จัดการฝ่ายขาย"),
        ("revise", "การแก้ไขใบเสนอราคา", "ลูกค้าขอแก้สเปกให้ออกใบเสนอราคาฉบับใหม่ ยกเลิกฉบับเดิม ห้ามแก้ทับ"),
        ("followup", "การติดตามใบเสนอราคา", "ติดตามลูกค้าภายใน 7 วันหลังส่งใบเสนอราคา บันทึกผลใน CRM"),
    ]),
    ("SOP-RECALL-852", "RECALL", "qc", [
        ("recall-steps", "ขั้นตอนการเรียกคืนผลิตภัณฑ์ (QP-852)",
         "เมื่อพบสินค้าไม่ได้มาตรฐานที่ส่งถึงลูกค้าแล้ว ให้แจ้ง QMR เปิด recall notice ระบุ lot ที่กระทบและติดตามคืน"),
        ("mock-recall", "การซ้อม Mock Recall", "ซ้อม mock recall อย่างน้อยปีละ 1 ครั้ง วัดเวลาติดตามสินค้าคืนไม่เกิน 4 ชั่วโมง"),
        ("recall-report", "รายงานผลการเรียกคืน", "สรุปจำนวนที่เรียกคืนได้เทียบยอดที่กระจาย รายงานต่อผู้บริหารภายใน 3 วัน"),
    ]),
    ("SOP-HR-TRAIN", "HR", "hr", [
        ("new-emp", "หัวข้อการอบรมพนักงานใหม่ (iso_jd)",
         "พนักงานใหม่ต้องอบรม safety, คุณภาพ ISO, และ JD ของตำแหน่งภายใน 30 วันแรก"),
        ("salary", "โครงสร้างเงินเดือนและสวัสดิการ", "เงินเดือน ค่ากะ และโบนัสประจำปีตามผลประเมิน KPI"),
        ("leave", "ระเบียบการลา", "ลาป่วยต้องมีใบรับรองแพทย์เมื่อลาเกิน 3 วัน ลากิจล่วงหน้า 1 วัน"),
    ]),
    ("SOP-PUR-741", "PURCHASING", "purchasing", [
        ("po-steps", "ขั้นตอนการจัดซื้อวัตถุดิบ (QP-741)",
         "เปิด PR เมื่อ stock ต่ำกว่า reorder point จัดซื้อขออนุมัติผู้จัดการก่อนออก PO ให้ vendor ที่อนุมัติ"),
        ("vendor", "การประเมิน vendor", "ประเมิน vendor ทุก 6 เดือนด้านคุณภาพ ราคา และการส่งมอบตรงเวลา"),
        ("urgent", "การจัดซื้อเร่งด่วน", "กรณีเร่งด่วนใช้ vendor สำรองได้ แต่ต้องแจ้งเหตุผลและขออนุมัติย้อนหลังภายใน 24 ชม."),
    ]),
    ("SOP-QC-423", "QUALITY", "qc", [
        ("fsc", "การควบคุมสัญลักษณ์ FSC (WI-423)",
         "ตรวจสัญลักษณ์ FSC บนงานพิมพ์ให้ตรงใบอนุญาต ห้ามใช้กับงานที่ไม่ได้ certified"),
        ("color", "การตรวจสีงานพิมพ์", "วัดค่า Delta-E เทียบ proof ที่ลูกค้าอนุมัติ ยอมรับได้ไม่เกิน 3.0"),
        ("defect", "เกณฑ์ของเสียงานพิมพ์", "งานที่มีจุดสกปรก เหลื่อมสี หรือหมึกเลอะเกินเกณฑ์ AQL ถือเป็นของเสีย"),
    ]),
    ("SOP-PROD-710", "PRODUCTION", "production", [
        ("press-setup", "การตั้งค่าเครื่องพิมพ์ (QP-710)",
         "ตั้งค่า register หมึก และแรงกดตามใบสั่งงาน ทดพิมพ์ก่อนผลิตจริงและให้ QC อนุมัติ first article"),
        ("makeready", "ขั้นตอน make-ready", "เตรียมเพลท หมึก และกระดาษให้ตรง job ลดเวลา setup ด้วย checklist"),
        ("waste", "การควบคุมของเสียในการผลิต", "บันทึกยอด waste ต่อ job เทียบเป้าหมายไม่เกิน 5%"),
    ]),
    ("SOP-LOG-755", "LOGISTICS", "logistics", [
        ("ship-steps", "ขั้นตอนการจัดส่งและ export (WI-755-05)",
         "จัดเตรียมเอกสาร packing list และ invoice ตรวจจำนวนก่อนขึ้นรถ ยืนยันกับลูกค้าปลายทาง"),
        ("plan", "การวางแผนขนส่ง", "รวม order ปลายทางเดียวกันเพื่อลดเที่ยว ประหยัดค่าขนส่ง"),
    ]),
    ("SOP-ENG-DOBOT", "ENGINEERING", "engineering", [
        ("maint", "การบำรุงรักษาเครื่องจักร dobot",
         "บำรุงรักษาเชิงป้องกันตามรอบ ตรวจ sensor และหล่อลื่นข้อต่อทุกสัปดาห์ บันทึกใน log"),
        ("spec", "แบบวิศวกรรมและ spec เครื่อง", "spec แรงบิดและระยะการเคลื่อนที่ของแขนกลตามคู่มือผู้ผลิต"),
    ]),
    ("SOP-PKG-755-06", "PACKAGING", "production", [
        ("box", "การผลิตกล่องลูกฟูก (WI-755-06)",
         "ขึ้นรูปกล่องลูกฟูกตามแบบ ตรวจความแข็งแรงขอบและรอยพับก่อนบรรจุ"),
        ("wrap", "การห่อและรัดพาเลท", "รัดฟิล์มพาเลทให้แน่นกันสินค้าล้มระหว่างขนส่ง"),
    ]),
    ("SOP-IT-MGR", "IT_SYSTEMS", "it", [
        ("login", "การเข้าใช้ระบบ Manager AE",
         "ผู้ใช้ต้อง login ด้วยบัญชีบริษัท เปลี่ยนรหัสผ่านทุก 90 วัน ห้ามแชร์บัญชี"),
        ("backup", "การสำรองข้อมูลระบบ", "สำรองฐานข้อมูลทุกคืน เก็บ 30 วัน ทดสอบ restore ทุกไตรมาส"),
    ]),
]

# ── HARD-NEGATIVE docs: lexical overlap สูงแต่ผิดเอกสาร/ผิด code (Codex เพิ่ม) ───
HARD_NEG_DOCS = [
    ("SOP-SALES-722", "SALES", "sales", [
        ("job-pkg", "การจัดทำ JOB Packaging (WI-722)",
         "จัดทำใบ JOB สำหรับงานบรรจุภัณฑ์ ระบุสเปกกล่องและจำนวน ไม่ใช่ขั้นตอนเสนอราคา"),  # lexical overlap กับ SALES-721
    ]),
    ("SOP-QC-760", "QUALITY", "qc", [
        ("cal", "การสอบเทียบเครื่องมือวัด (QP-760)",
         "สอบเทียบเครื่องวัดสีและตาชั่งตามรอบ ไม่เกี่ยวกับเกณฑ์ของเสีย"),  # sibling ของ QC-423
    ]),
]


def build():
    corpus, key_to_pid = {}, {}
    for doc_id, coll, _role, chunks in DOCS + HARD_NEG_DOCS:
        pay = payload_for(coll)
        for ck, heading, text in chunks:
            k = f"{doc_id}:{ck}"
            p = pid(k)
            key_to_pid[k] = p
            corpus[p] = {"source": doc_id, "rerank_text": f"{heading} {text}", "payload": dict(pay)}
    return corpus, key_to_pid


def make_case(qid, query, role, answer_keys, doc_id, lang, category, split, key_to_pid, grade=3):
    rel = {key_to_pid[k]: grade for k in answer_keys}
    return {"query_id": qid, "query": query, "role": role, "lang": lang, "category": category,
            "split": split, "case_type": "ranking", "relevance": rel,
            "relevant_sources": [doc_id], "label_status": "draft"}


# ── QUERIES (by-construction) — templates ต่อ doc + hard-negative families ──────
# (doc_idx, answer_chunk_key, query_th, query_theng, category)
QSPECS = [
    (0, "quote-steps", "ขั้นตอนการเสนอราคางานพิมพ์ทำอย่างไร", "quote งานพิมพ์ มีขั้นตอนอะไรบ้าง", "direct"),
    (0, "approve", "ใบเสนอราคาต้องขออนุมัติเมื่อไร", "quotation ต้อง approve ตอนไหน", "sibling-hard-negative"),
    (0, "revise", "ลูกค้าขอแก้สเปกต้องทำใบเสนอราคาอย่างไร", "revise quotation ทำยังไง", "current-superseded"),
    (1, "recall-steps", "ขั้นตอนการเรียกคืนผลิตภัณฑ์จากลูกค้า", "product recall procedure คืออะไร", "direct"),
    (1, "mock-recall", "การซ้อม mock recall ต้องทำบ่อยแค่ไหน", "mock recall ปีละกี่ครั้ง", "table-row"),
    (1, "recall-report", "ต้องรายงานผลการเรียกคืนภายในกี่วัน", "recall report ส่งเมื่อไร", "sibling-hard-negative"),
    (2, "new-emp", "การอบรมพนักงานใหม่มีหัวข้ออะไรบ้าง", "new employee training หัวข้ออะไร", "direct"),
    (2, "salary", "โครงสร้างเงินเดือนและโบนัสเป็นอย่างไร", "salary structure บริษัทเป็นยังไง", "sibling-hard-negative"),
    (2, "leave", "ลาป่วยเกินกี่วันต้องมีใบรับรองแพทย์", "sick leave เกินกี่วันต้องมีใบแพทย์", "table-row"),
    (3, "po-steps", "ขั้นตอนการจัดซื้อวัตถุดิบและการขออนุมัติ", "purchasing วัตถุดิบ ขั้นตอนอะไร", "direct"),
    (3, "vendor", "ต้องประเมิน vendor บ่อยแค่ไหน", "vendor evaluation ทุกกี่เดือน", "table-row"),
    (3, "urgent", "การจัดซื้อเร่งด่วนทำได้ไหม", "urgent purchase ทำยังไง", "sibling-hard-negative"),
    (4, "fsc", "การควบคุมสัญลักษณ์ FSC บนงานพิมพ์", "FSC logo control ทำยังไง", "direct"),
    (4, "color", "ค่า Delta-E ที่ยอมรับได้เท่าไร", "Delta-E ยอมรับได้เท่าไหร่", "table-row"),
    (4, "defect", "เกณฑ์ของเสียงานพิมพ์มีอะไรบ้าง", "print defect criteria คืออะไร", "sibling-hard-negative"),
    (5, "press-setup", "การตั้งค่าเครื่องพิมพ์ก่อนผลิต", "press setup ก่อนผลิตทำยังไง", "direct"),
    (5, "makeready", "ขั้นตอน make-ready ลดเวลา setup อย่างไร", "make-ready ลด setup time ยังไง", "sibling-hard-negative"),
    (5, "waste", "เป้าหมายของเสียในการผลิตไม่เกินเท่าไร", "production waste เป้าไม่เกินกี่ %", "table-row"),
    (6, "ship-steps", "ขั้นตอนการจัดส่งและ export", "shipping export procedure คืออะไร", "direct"),
    (6, "plan", "การวางแผนขนส่งลดค่าใช้จ่ายอย่างไร", "transport planning ลดต้นทุนยังไง", "sibling-hard-negative"),
    (7, "maint", "การบำรุงรักษาเครื่องจักร dobot ทำอย่างไร", "dobot maintenance ทำยังไง", "direct"),
    (7, "spec", "spec แรงบิดของแขนกลอยู่ที่เท่าไร", "torque spec แขนกล dobot", "sibling-hard-negative"),
    (8, "box", "การผลิตกล่องลูกฟูกตรวจอะไรบ้าง", "corrugated box production ตรวจอะไร", "direct"),
    (8, "wrap", "การรัดฟิล์มพาเลทกันสินค้าล้มทำอย่างไร", "pallet wrap กันล้มยังไง", "sibling-hard-negative"),
    (9, "login", "การเข้าใช้ระบบ Manager AE ต้องทำอย่างไร", "Manager AE login policy", "direct"),
    (9, "backup", "การสำรองข้อมูลระบบทำบ่อยแค่ไหน", "system backup ทุกกี่วัน", "table-row"),
    # acronym / transliteration Thai <-> English (Codex เพิ่ม)
    (0, "followup", "ต้องติดตามลูกค้าหลังส่งใบเสนอราคาภายในกี่วัน", "quotation follow-up ภายในกี่วัน", "table-row"),
    (3, "po-steps", "PR และ PO ต่างกันอย่างไรในการจัดซื้อ", "PR vs PO ในการ purchase", "acronym-transliteration"),
    (2, "new-emp", "พนักงานใหม่ต้องอบรม ISO และ KPI ไหม", "new emp ISO KPI training", "acronym-transliteration"),
    (4, "color", "การวัดค่า Delta-E เทียบ proof ทำอย่างไร", "measure Delta-E vs proof", "acronym-transliteration"),
    # negation / current-superseded (Codex เพิ่ม)
    (5, "waste", "งานที่ waste เกิน 5% ถือว่าไม่ผ่านใช่ไหม", "waste เกิน 5% ไม่ผ่านใช่ไหม", "negation"),
    (4, "defect", "งานที่ไม่มีจุดสกปรกถือว่าผ่านเกณฑ์ใช่ไหม", "งานไม่มี defect ผ่านไหม", "negation"),
    (1, "recall-steps", "ถ้าสินค้ายังไม่ถึงลูกค้าต้องเปิด recall ไหม", "ยังไม่ส่งลูกค้า ต้อง recall ไหม", "negation"),
    (3, "urgent", "การจัดซื้อปกติใช้ vendor สำรองได้ไหม", "งานปกติใช้ backup vendor ได้ไหม", "negation"),
]

# graded relevance (2 relevant chunk: primary grade 3 + related grade 2) — สำหรับ nDCG graded
GRADED_QSPECS = [
    (0, [("quote-steps", 3), ("approve", 2)], "sales",
     "การเสนอราคาและการอนุมัติใบเสนอราคาทำอย่างไร", "quotation และ approval flow", "graded-multi"),
    (1, [("recall-steps", 3), ("recall-report", 2)], "qc",
     "ขั้นตอนการเรียกคืนและการรายงานผล", "recall steps และ report", "graded-multi"),
    (3, [("po-steps", 3), ("vendor", 2)], "purchasing",
     "การจัดซื้อและการประเมิน vendor", "purchasing และ vendor evaluation", "graded-multi"),
]

# lexical-overlap-wrong-doc: query คล้าย SALES-721 แต่คำตอบคือ WI-722 (คนละ code)
LEX_QSPECS = [
    ("SOP-SALES-722", "job-pkg", "sales", "การจัดทำ JOB Packaging WI-722 ทำอย่างไร",
     "WI-722 job packaging ทำยังไง", "lexical-overlap-wrong-code"),
]


def build_cases(key_to_pid):
    cases = []
    n = 0
    for di, ck, q_th, q_theng, cat in QSPECS:
        doc_id, role = DOCS[di][0], DOCS[di][2]
        cases.append(make_case(f"q-{n:03d}", q_th, role, [f"{doc_id}:{ck}"], doc_id, "th", cat, "test", key_to_pid))
        n += 1
        cases.append(make_case(f"q-{n:03d}", q_theng, role, [f"{doc_id}:{ck}"], doc_id, "th-en",
                               "thai-eng-mix", "test", key_to_pid))
        n += 1
    for doc_id, ck, role, q_th, q_theng, cat in LEX_QSPECS:
        cases.append(make_case(f"q-{n:03d}", q_th, role, [f"{doc_id}:{ck}"], doc_id, "th", cat, "test", key_to_pid))
        n += 1
        cases.append(make_case(f"q-{n:03d}", q_theng, role, [f"{doc_id}:{ck}"], doc_id, "th-en",
                               "thai-eng-mix", "test", key_to_pid))
        n += 1
    for di, graded, role, q_th, q_theng, cat in GRADED_QSPECS:
        doc_id = DOCS[di][0]
        rel = {key_to_pid[f"{doc_id}:{k}"]: g for k, g in graded}
        for q, lang in ((q_th, "th"), (q_theng, "th-en")):
            cases.append({"query_id": f"q-{n:03d}", "query": q, "role": role, "lang": lang,
                          "category": cat, "split": "test", "case_type": "ranking",
                          "relevance": rel, "relevant_sources": [doc_id], "label_status": "draft"})
            n += 1
    # split: ทุก ๆ ตัวที่ 5 เป็น dev (กระจายทุก category), ที่เหลือ test — ต้อง test >= 50
    for i, c in enumerate(cases):
        c["split"] = "dev" if i % 5 == 0 else "test"
    return cases


def main():
    corpus, key_to_pid = build()
    cases = build_cases(key_to_pid)
    with open("p2_corpus.json", "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=1)
    with open("p2_eval_set.json", "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)

    known = set(P.KNOWN_ROLES)
    errs = E.validate_ranking_eval_set(cases, corpus, known)
    only_label = [e for e in errs if "label_status" not in e]
    dev = [c for c in cases if c["split"] == "dev"]
    test = [c for c in cases if c["split"] == "test"]
    cats = sorted({c["category"] for c in cases})
    print(f"corpus points : {len(corpus)}")
    print(f"cases total   : {len(cases)} (dev {len(dev)}, test {len(test)})  [Codex ต้อง test >= 50]")
    print(f"categories    : {cats}")
    print(f"corpus valid  : {E.validate_corpus(corpus) == []}")
    print(f"cases errors (ยกเว้น label_status ที่ตั้งใจ draft): {len(only_label)}")
    for e in only_label[:10]:
        print(f"  - {e}")
    print(f"label_status  : draft (รอ human/Codex review เลื่อนเป็น human-reviewed)")
    print(f"eval_set hash : {E.eval_set_sha256(cases)[:16]}  corpus hash: {E.corpus_manifest_sha256(corpus)[:16]}")


if __name__ == "__main__":
    main()
