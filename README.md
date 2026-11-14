# 🐉 Baby Dragon — Lottie POC (Figma → SVG → Lottie)

มังกรเบบี้ตัวเดิม (ทีม Rive) **เวอร์ชันสวยขึ้น** ทำเป็น **Lottie** ด้วย pipeline:

```
Figma (AI vector illustration, แยกเลเยอร์ Wing/Tail/Head/Body/Eyes/Sparkles)
  → download_assets (SVG, เก็บ gradient)
  → tools/gen_lottie.py แปลง SVG → Lottie JSON + rig (pivot, keyframes)
  → lottie/dragon_{idle,happy,celebrate}.json   (vector 100%, ~88KB ตัว)
  → เปิด preview.html ดูได้เลย (ฝัง lottie.min.js + JSON ในไฟล์เดียว)
```

- Figma file: https://www.figma.com/design/5ulP0nwuzZJ14IMEnGfjKK (Baby Dragon Lottie POC)
- เปิดดู: [preview.html](preview.html) — ปุ่มสลับ Idle / Happy / Celebrate (loop 3s @ 60fps)

## Rig (แยกเลเยอร์ Lottie 15 ชิ้น)

| Layer | Animation |
|---|---|
| Bob (null, parent ทุกชิ้น) | ยกตัว -12px + squash ตอน idle / เด้ง -66px / กระโดด -150px |
| Sparkle ×6 | twinkle opacity 25↔100 + scale เฟสสลับกัน |
| Eye L/R | กะพริบ 2 ครั้ง/รอบ (scaleY 100→5) |
| Head | เอียง ±1.6° + ลอย |
| Body | หายใจ scaleY 101.6 |
| Tail | แกว่ง ±4° |
| Wing L/R | ลอย ±7° / กระพือ ±16° / ±20° ตอน celebrate |
| Shadow | หด-ขยาย sync กับการลอย |

## วิธี regen

```bash
python3 tools/gen_lottie.py svg/dragon_full.svg lottie     # SVG → JSON 3 variants
python3 tools/render_frames.py idle 0.5,1.5,2.5            # render เฟรมพิสูจน์ (playwright)
open preview.html
```

## Gotchas ที่เจอ (สำคัญมาก ถ้าจะแก้ต่อ)

1. **Lottie shapes: ตัวแรกวาดหน้าสุด** (เหมือน Rive!) — SVG document order ต้อง reverse
   ก่อนยัดเข้า `shapes[]` ไม่งั้นเส้นสติกเกอร์ขาวทับทั้งตัว
2. **สีใน Lottie JSON (version ≥ 5) เป็น 0–1** — lottie-web คูณ 255 เอง ใส่ 0–255 = ล้น
   → browser clamp เป็นขาวหมด (เจอยากเพราะ DOM ดู "ปกติ")
3. **easing arrays ต้องยาว = จำนวน dimension ของ value** (scale 3D → x:[0.42,0.42,0.42])
   สั้นกว่านั้น lottie-web คำนวณ matrix เป็น NaN เงียบ ๆ
4. keyframes ต้องเรียงตาม t (เคยเขียน celebrate ไม่ sort → NaN)
5. `goToAndStop` + เลเยอร์ static บางกรณีค้าง `display:none` — ใช้ autoplay แล้วค่อย scrub
   ในเวิร์กโฟลว์เรนเดอร์เฟรม
6. SVG จาก Figma: gradient เป็น `userSpaceOnUse` absolute (ไม่ใช่ objectBoundingBox),
   path มีแค่ M/L/C/Z, ไม่มี transform — แปลงตรง ๆ ได้เลย

## State Machine (dragon_sm.json)

Lottie ไม่มี state machine ในตัว (ต่างจาก Rive) — จึงทำตาม pattern มาตรฐานของ Lottie:

- ไฟล์เดียว `lottie/dragon_sm.json` (~285KB): **45 layers = 3 อารมณ์ซ้อนกัน**
  keyframes ของ happy/celebrate ถูกเลื่อนไปกองที่ segment ของตัวเอง
- timeline 576 เฟรม: `idle [0-180] → to_happy [180-192] → happy [192-372] →
  to_celebrate [372-384] → celebrate [384-564] → to_idle [564-576]` + **markers** ป้ายชื่อ
- ทรานสิชัน = crossfade 12 เฟรม (opacity ต่อเลเยอร์) — เข้าเป้า fade-in ตอน `to_<ตัวเอง>`
  fade-out ตอน `to_<ตัวถัดไป>` → **เรียกจาก state ไหนก็ได้** (ทุก state จบที่ rest pose เหมือนกัน)
- **JS host เป็นตัว state machine**: `dragon.setEmotion('happy')` → playSegments([blend, state])
  เล่นจบแล้ววน loop ที่ state ใหม่ (อ่าน segment จาก markers ใน JSON)

```js
window.dragon.setEmotion('happy')   // crossfade 200ms แล้ว loop happy ตลอดไป
```

เปิด `preview.html` กดปุ่มสลับ หรือกด "Auto demo" ให้วนเอง

### gotchas เพิ่มเวลาทำ state machine

- **อย่าใช้ precomp (ty:0) สำหรับ state ที่เลื่อนเวลา**: lottie-web SVG CompElement
  เล่นเนื้อหาตามเวลา main comp ตรง ๆ (`st` ไม่ถูกใช้) — segment ที่ 192+ เลยความยาว
  precomp = ว่าง; แถม clipPath ของ precomp สร้างจาก `w/h` **ของ layer** (ไม่ใส่ →
  `Lundefined` → clip ว่าง → มองไม่เห็นทั้งก้อน) — flat layers + shift keys ปลอดภัยกว่า
- sparkles ที่มี opacity animation ของตัวเอง ต้องย้าย twinkle ไปที่ shape-level `tr.o`
  เพื่อให้ layer `ks.o` ว่างสำหรับ state timeline
