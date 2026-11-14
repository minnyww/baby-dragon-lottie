import sys, json, time
from playwright.sync_api import sync_playwright

variant = sys.argv[1]
times = [float(t) for t in sys.argv[2].split(",")]
data = json.load(open(f"lottie/dragon_{variant}.json"))
html = """<!doctype html><html><body style="margin:0;background:#ffffff">
<div id="stage" style="width:1024px;height:1024px"></div>
<script>""" + open('tools/lottie.min.js').read() + """</script>
<script>
window.anim = lottie.loadAnimation({container: document.getElementById('stage'),
  renderer:'svg', loop:true, autoplay:true, animationData: """ + json.dumps(data) + """});
window.t0 = performance.now();
</script></body></html>"""
open('tools/_view.html','w').write(html)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':1024,'height':1024})
    pg.goto('file:///Users/min/Desktop/playground/poc-lottie/tools/_view.html')
    pg.wait_for_function("window.anim && window.anim.totalFrames > 0")
    start = time.time()
    for t in sorted(times):
        target = start + t
        while time.time() < target:
            pg.wait_for_timeout(40)
        frame = pg.evaluate("window.anim.currentFrame.toFixed(1)")
        pg.screenshot(path=f"verify/{variant}_{t:.2f}s.png")
        print(f"{variant} t={t}s (frame {frame}) ok")
    b.close()
