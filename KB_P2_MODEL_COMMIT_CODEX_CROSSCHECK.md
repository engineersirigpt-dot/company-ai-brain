# Codex Cross-check — Hugging Face commit สำหรับ P2 reranker

**Model:** `BAAI/bge-reranker-v2-m3`  
**Decision:** **PIN** `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`  
**Scope of evidence:** ตัดสินจาก commit list + `revision/main` tree facts ที่ผู้พัฒนาดึงจาก HF API และส่งมาใน handoff เท่านั้น; ไม่ได้สร้างหรือเดา SHA เพิ่ม

## คำตอบ

### 1. Pin HEAD `953dc6f...` ถูกต้องไหม แม้ commit ล่าสุดแตะ README/metadata

**ถูกต้อง** สำหรับ reproducible snapshot

Git commit บันทึก **tree state ทั้ง repository** ณ commit นั้น ไม่ได้บันทึกเพียงไฟล์ที่เปลี่ยนใน commit message ดังนั้น snapshot ที่ revision:

```text
953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
```

จะอ้าง tree ซึ่งรวม weights/tokenizer/config จาก commit ก่อนหน้าที่ยังอยู่ใน repository ด้วย การที่ commit ล่าสุดแก้ README/metadata ไม่ทำให้ไฟล์ model หาย

จากข้อเท็จจริงที่ให้มา `revision/main` ณ SHA นี้ยืนยันว่ามีไฟล์บังคับครบทั้ง 6:

- `config.json`
- `model.safetensors`
- `tokenizer.json`
- `tokenizer_config.json`
- `sentencepiece.bpe.model`
- `special_tokens_map.json`

จึงเป็น SHA ที่มีหลักฐาน completeness แข็งที่สุดในลิสต์

### 2. ควร pin commit ที่ weights ลงครั้งแรก `324cc405...` แทนหรือไม่

**ไม่แนะนำจากหลักฐานชุดนี้**

ข้อดีของ `324cc40576b08b305b9c65a867c26c173a477ae2` คืออยู่ใกล้เหตุการณ์ upload weights ครั้งแรกและลดการรวม metadata changes ภายหลัง แต่ไม่มีประโยชน์ด้าน reproducibility เหนือ full SHA ของ HEAD เพราะทั้งสองเป็น immutable snapshots เหมือนกัน

ข้อเสียสำคัญคือ commit list ระบุว่า `config.json` ถูก upload ใน commitถัดมา:

```text
995ec6ee29e8a96b27eee66c584c4340104ab8e5
```

ดังนั้น tree ที่ `324cc405...` อาจยังไม่ครบ runtime contract โดยเฉพาะ `config.json` และข้อมูลที่ให้มายืนยัน completeness เฉพาะ tree ณ `953dc6f...` เท่านั้น หากจะเลือก `324cc405...` ต้อง query tree ของ SHA นั้นและพิสูจน์ไฟล์/manifest ใหม่ ซึ่งไม่มีเหตุผลให้เพิ่มความเสี่ยงในรอบนี้

### 3. `tokenizer_commit = model_commit` ถูกไหม

**ถูกต้อง** สำหรับการโหลด model และ tokenizer จาก repository snapshot เดียวกันนี้

ให้ตั้งทั้งคู่เป็น:

```text
953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
```

ความหมายคือ model weights/config และ tokenizer artifacts ถูก freeze ที่ tree เดียวกัน หากภายหลัง tokenizer ถูกย้ายไปคนละ repository จึงค่อยมี commit แยก แต่ไม่ใช่กรณีนี้

### 4. Gotchas ก่อน bake image

1. **Build-time กับ runtime offline เป็นคนละช่วง** — build stage ต้อง prefetch snapshot ด้วย SHA ขณะมี network/cache; runtime จึงใช้ `local_files_only=True`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` ได้ หากเปิด offline ก่อน cache มี snapshot การ build/load จะ fail ตามที่ควร
2. **รักษา HF cache layout ให้ตรง loader** — `p2_reranker._resolved_commit()` ตรวจ basename ของ path ว่าเป็น SHA ดังนั้น snapshot ใน image ต้องยังอยู่ใต้ `snapshots/<SHA>` หรือปรับ verification ให้ตรวจ baked manifest แทน ห้าม copy ไป arbitrary directory แล้วคาดว่า assertion เดิมจะผ่าน
3. **ระวัง symlink/cache blobs** — HF cache มักแยก `snapshots/` กับ `blobs/`; Docker multi-stage copy ต้องนำ actual blobs ที่ snapshot อ้างถึงมาครบ ไม่ใช่เหลือ broken symlinks
4. **ตรวจว่าเป็น actual LFS blob ไม่ใช่ pointer** — verify `model.safetensors` ด้วย file size/hash และทำ model-load smoke ใน image
5. **ตรวจ required files หลัง download** — fail build ทันทีหาก 1 ใน 6 ไฟล์หาย และสร้าง canonical file-manifest SHA-256 จาก snapshot จริง
6. **Pin environment ด้วย** — base image digest และ versions ของ `torch`, `transformers`, `huggingface_hub`, `safetensors`, `sentencepiece` ต้องถูก lock เพราะ model SHA อย่างเดียวไม่ทำให้ inference reproducible ทั้งระบบ
7. **บันทึก image digest หลัง build** — RunPlan ต้องใช้ immutable image digestจริง ไม่ใช้ tag เช่น `p2:latest`
8. **ไม่ใช้ tag/branch เป็น fallback** — หาก SHA โหลดไม่ได้ ให้ fail build; ห้าม fallback ไป `main` เพราะจะทำให้ evidence เปลี่ยนโดยไม่รู้ตัว
9. **Model-load smoke ยังจำเป็น** — completeness ของ tree ไม่รับประกันว่า dependency versions/device/dtype ที่เลือกโหลดได้ ต้องพิสูจน์ resolved SHA, tokenizer load, model load และ scoring finite ใน container

## ค่าแนะนำสำหรับ RunPlan

```text
model_name      = BAAI/bge-reranker-v2-m3
model_commit    = 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
tokenizer_commit= 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e
```

`model_file_manifest_sha256` ต้องคำนวณจาก snapshot ที่ bake จริง และ `image_digest` ต้องกรอกหลัง build สำเร็จ จึงห้ามเดาค่าสองตัวนี้ล่วงหน้า

## Verdict

**GO — pin `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`.** เหตุผลหลักคือเป็น immutable full-tree snapshot และเป็น SHA เดียวในหลักฐานที่ให้มาซึ่งยืนยัน required file set ครบทั้งหมด ส่วน `324cc405...` ไม่มีข้อได้เปรียบด้าน reproducibility และอาจอยู่ก่อน `config.json` พร้อมใช้งาน
