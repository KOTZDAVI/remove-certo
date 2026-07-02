import os
import io
import sys
import uuid
import zipfile

# Limita threads antes de importar numpy/onnxruntime para não travar o PC
_THREADS = str(min(4, os.cpu_count() or 4))
os.environ.setdefault("OMP_NUM_THREADS", _THREADS)
os.environ.setdefault("OPENBLAS_NUM_THREADS", _THREADS)
os.environ.setdefault("MKL_NUM_THREADS", _THREADS)

# Suporte a execução empacotada (PyInstaller)
_FROZEN = getattr(sys, "frozen", False)
if _FROZEN:
    _BASE_DIR = os.path.dirname(sys.executable)
    _TEMPLATE_DIR = os.path.join(sys._MEIPASS, "templates")
    # Modelos do rembg ficam na pasta "models" ao lado do executável
    os.environ.setdefault("REMBG_HOME", os.path.join(_BASE_DIR, "models"))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _TEMPLATE_DIR = os.path.join(_BASE_DIR, "templates")

import numpy as np
import fitz
from flask import Flask, render_template, request, jsonify, send_file
from rembg import remove, new_session
from PIL import Image
from scipy import ndimage

app = Flask(__name__, template_folder=_TEMPLATE_DIR)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# Tamanho máximo para enviar à IA — imagens maiores são reduzidas antes do modelo
# e o resultado é escalado de volta. Reduz RAM e tempo drasticamente.
AI_MAX_PX    = 1024  # resolução para inferência da IA
MATTING_MAX  = 2048  # resolução máxima para o refinamento de borda (pymatting)

RESULT_FOLDER      = os.path.join(_BASE_DIR, "results")
ORIGINAL_FOLDER    = os.path.join(_BASE_DIR, "originals")
BACKGROUNDS_FOLDER = os.path.join(_BASE_DIR, "backgrounds")
os.makedirs(RESULT_FOLDER, exist_ok=True)
os.makedirs(ORIGINAL_FOLDER, exist_ok=True)
os.makedirs(BACKGROUNDS_FOLDER, exist_ok=True)

def apply_upscale(img: Image.Image, factor: int) -> Image.Image:
    """Upscale HD: multi-pass Lanczos + edge-preserving enhancement via OpenCV."""
    import cv2
    r, g, b, a = img.split()
    nw, nh = img.width * factor, img.height * factor

    rgb = Image.merge("RGB", (r, g, b))
    # Multi-pass para 4x: dois passos de 2x preservam mais detalhe nas curvas
    if factor == 4:
        rgb = rgb.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    rgb = rgb.resize((nw, nh), Image.LANCZOS)

    rgb_cv = np.array(rgb, dtype=np.uint8)
    # Detail enhance (sigma_s: spatial, sigma_r: range)
    enhanced = cv2.detailEnhance(rgb_cv, sigma_s=12, sigma_r=0.12)
    # Unsharp mask leve
    blur = cv2.GaussianBlur(enhanced, (0, 0), 1.5)
    sharpened = cv2.addWeighted(enhanced, 1.4, blur, -0.4, 0)
    sharpened = np.clip(sharpened, 0, 255).astype(np.uint8)

    rgb_out = Image.fromarray(sharpened)
    a_up = a.resize((nw, nh), Image.LANCZOS)
    return Image.merge("RGBA", (*rgb_out.split(), a_up))

rembg_session = None
# isnet-general-use: ótimo equilíbrio qualidade/velocidade na CPU (já baixado, 171MB)
# birefnet-general: qualidade máxima mas muito lento sem GPU (973MB)
for _model in ["isnet-general-use", "u2net"]:
    try:
        rembg_session = new_session(_model)
        print(f"Modelo IA: {_model}")
        break
    except Exception as _e:
        print(f"  {_model} falhou: {_e}")

ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "bmp"}


def _build_trimap(mask_f32: np.ndarray, erode_px: int) -> np.ndarray:
    """
    Trimap com preservação de estruturas finas (cabelos, linhas, bordas de texto).

    Problema clássico: erosão consume features < 2*erode_px pixels de largura,
    jogando-as na zona incerta onde o pymatting pode decidir pela cor errada e cortá-las.

    Solução:
    - Abertura morfológica (erode→dilate) remove do fg tudo mais fino que 2*erode_px.
    - O que existia no fg mas não sobreviveu à abertura = estruturas finas.
    - Essas estruturas → foreground garantido (1.0), não entram na zona incerta.
    - A zona incerta (0.5) fica apenas nas bordas de estruturas GROSSAS, onde o
      pymatting tem contexto suficiente para refinar corretamente.
    """
    fg = mask_f32 > 0.90
    bg = mask_f32 < 0.10

    # Núcleo de foreground sólido (o que sobra após erosão profunda)
    fg_core = ndimage.binary_erosion(fg, iterations=erode_px)
    # Núcleo de background sólido
    bg_core = ndimage.binary_erosion(bg, iterations=erode_px)

    # Abertura = erosão seguida de dilatação: remove estruturas finas < 2*erode_px
    # O que fg tinha mas abertura removeu = linhas finas, cabelos, texto, etc.
    opened = ndimage.binary_opening(fg, iterations=erode_px)
    thin_structures = fg & ~opened   # features finas detectadas

    # Foreground garantido = núcleo sólido + todas as estruturas finas preservadas
    fg_definite = fg_core | thin_structures

    return np.where(fg_definite, 1.0, np.where(bg_core, 0.0, 0.5))


def remove_ai(image: Image.Image) -> Image.Image:
    """
    Pipeline de remoção de fundo em 3 etapas:

    1. Inferência em resolução reduzida (AI_MAX_PX) → máscara bruta rápida.
    2. Alpha matting de alta resolução (cap MATTING_MAX) com trimap inteligente:
       - Estruturas finas (cabelos, linhas) → foreground garantido (não entram na
         zona incerta do pymatting, evitando cortes acidentais).
       - Bordas de estruturas grossas → zona incerta refinada pelo pymatting.
    3. Suavização seletiva apenas na zona de transição; pixels sólidos intocados.
    """
    from pymatting import estimate_alpha_cf

    w, h = image.size

    # ── Etapa 1: inferência IA ────────────────────────────────────
    inf_scale = AI_MAX_PX / max(w, h) if max(w, h) > AI_MAX_PX else 1.0
    small = image.resize((round(w * inf_scale), round(h * inf_scale)), Image.LANCZOS) \
            if inf_scale < 1.0 else image

    result_small = remove(small, session=rembg_session, post_process_mask=True)
    mask_small = np.array(result_small.split()[3], dtype=np.float32) / 255.0

    # ── Etapa 2: matting com trimap que preserva linhas finas ─────
    mat_scale = MATTING_MAX / max(w, h) if max(w, h) > MATTING_MAX else 1.0
    mw, mh = round(w * mat_scale), round(h * mat_scale)

    mat_img = image.resize((mw, mh), Image.LANCZOS) if mat_scale < 1.0 else image
    mat_rgb = np.array(mat_img.convert("RGB"), dtype=np.float64) / 255.0

    mask_up = Image.fromarray((mask_small * 255).astype(np.uint8), "L") \
                   .resize((mw, mh), Image.LANCZOS)
    mask_f = np.array(mask_up, dtype=np.float32) / 255.0

    # erode_px define a largura da zona incerta nas bordas grossas.
    # Estruturas < 2*erode_px de largura são detectadas e protegidas pelo trimap.
    erode_px = max(4, round(min(mw, mh) * 0.012))
    trimap = _build_trimap(mask_f, erode_px)

    try:
        alpha = np.clip(estimate_alpha_cf(mat_rgb, trimap), 0.0, 1.0)
    except Exception:
        alpha = mask_f  # fallback se pymatting falhar

    # ── Etapa 3: limpeza e escala ao tamanho original ─────────────
    alpha_img = Image.fromarray((alpha * 255).astype(np.uint8), "L")
    if mat_scale < 1.0:
        alpha_img = alpha_img.resize((w, h), Image.LANCZOS)

    alpha_arr = np.array(alpha_img, dtype=np.float32)
    # Suaviza apenas a zona de transição (5–250); foreground/bg sólidos não são tocados
    smooth = ndimage.gaussian_filter(alpha_arr, sigma=0.5)
    edge_zone = (alpha_arr > 5) & (alpha_arr < 250)
    alpha_arr[edge_zone] = smooth[edge_zone]
    alpha_arr = np.clip(alpha_arr, 0, 255).astype(np.uint8)

    orig_rgba = image.convert("RGBA")
    orig_rgba.putalpha(Image.fromarray(alpha_arr))
    return orig_rgba


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMG


def remove_white_bg(image: Image.Image, tolerance: int = 15) -> Image.Image:
    """
    Remove fundo branco em dois passos:
    1. Flood fill rígido a partir das bordas (captura o fundo principal).
    2. Expansão 3px p/ limpar anti-aliasing, com alpha suave na zona de borda.

    Proteção por saturação: pixels com cor visível (rosa, amarelo, azul pálido…)
    NÃO são classificados como fundo mesmo sendo claros — evita cortar traços finos
    de artes com cores próximas ao branco.
    """
    img = image.convert("RGBA")
    data = np.array(img, dtype=np.uint8)

    r = data[:, :, 0].astype(np.float32)
    g = data[:, :, 1].astype(np.float32)
    b = data[:, :, 2].astype(np.float32)

    # Saturação HSV = (max - min) / max  → 0 = cinza/branco puro, 1 = cor pura
    max_ch = np.maximum(np.maximum(r, g), b)
    min_ch = np.minimum(np.minimum(r, g), b)
    sat = np.where(max_ch > 0, (max_ch - min_ch) / (max_ch + 1e-6), 0.0)

    # Só remove pixels que são TANTO claros QUANTO sem cor (achromáticos).
    # Traços coloridos claros (rosa, salmão, amarelo pálido…) têm sat > 0.08
    # e serão preservados mesmo que R,G,B estejam próximos de 255.
    achromatic = sat < 0.08

    ri = r.astype(np.int32)
    gi = g.astype(np.int32)
    bi = b.astype(np.int32)

    # Passo 1 – flood fill rígido a partir das bordas
    thr = 255 - tolerance
    white_mask = (ri >= thr) & (gi >= thr) & (bi >= thr) & achromatic
    labeled, _ = ndimage.label(white_mask)

    border_labels = set()
    border_labels.update(np.unique(labeled[0, :]).tolist())
    border_labels.update(np.unique(labeled[-1, :]).tolist())
    border_labels.update(np.unique(labeled[:, 0]).tolist())
    border_labels.update(np.unique(labeled[:, -1]).tolist())
    border_labels.discard(0)

    core_bg = np.isin(labeled, list(border_labels))
    bg_mask = core_bg.copy()

    # Passo 2 – expansão 3px para franja de anti-aliasing
    edge_thr = max(255 - tolerance * 4, 170)
    for _ in range(3):
        expanded = ndimage.binary_dilation(bg_mask)
        candidates = expanded & ~bg_mask
        near_white = (ri >= edge_thr) & (gi >= edge_thr) & (bi >= edge_thr) & achromatic
        bg_mask |= candidates & near_white

    expansion_mask = bg_mask & ~core_bg
    data[core_bg, 3] = 0

    ey, ex = np.where(expansion_mask)
    if len(ey) > 0:
        min_vals = np.minimum(np.minimum(ri[ey, ex], gi[ey, ex]), bi[ey, ex])
        scale = max(255 - edge_thr, 1)
        whiteness = np.clip((min_vals - edge_thr) / scale, 0.0, 1.0)
        data[ey, ex, 3] = np.round((1.0 - whiteness) * 255).astype(np.uint8)

    return Image.fromarray(data, "RGBA")


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/remove", methods=["POST"])
def remove_background():
    if "files" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    files = request.files.getlist("files")
    mode      = request.form.get("mode", "ai")
    tolerance = int(request.form.get("tolerance", 15))
    results   = []

    for file in files:
        if not file or file.filename == "":
            continue
        if not allowed_file(file.filename):
            results.append({"name": file.filename, "error": "Formato não suportado"})
            continue

        try:
            img_bytes   = file.read()
            input_image = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            base_name   = os.path.splitext(file.filename)[0]
            # Token único evita colisão quando várias pessoas usam o app ao mesmo tempo
            # (dois uploads de "foto.png" não podem sobrescrever o arquivo um do outro)
            token       = uuid.uuid4().hex[:8]

            # Salva original como JPEG (RGB, sem transparência → sem checkerboard)
            orig_filename = f"{base_name}_{token}_original.jpg"
            Image.open(io.BytesIO(img_bytes)).convert("RGB").save(
                os.path.join(ORIGINAL_FOLDER, orig_filename), "JPEG", quality=95
            )

            if mode == "white":
                output_image = remove_white_bg(input_image, tolerance=tolerance)
            elif mode == "none":
                # Edição direta: sem remoção de fundo, só prepara os arquivos p/ o editor
                output_image = input_image
            else:
                output_image = remove_ai(input_image)

            out_filename = f"{base_name}_{token}_sem_fundo.png"
            output_image.save(os.path.join(RESULT_FOLDER, out_filename), "PNG")

            results.append({
                "name": file.filename,
                "result": out_filename,
                "original": orig_filename,
                "error": None,
            })
        except Exception as e:
            results.append({"name": file.filename, "error": str(e)})

    return jsonify({"results": results})


@app.route("/export", methods=["POST"])
def export_files():
    import shutil
    data   = request.get_json()
    files  = data.get("files", [])
    folder = data.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "Pasta não especificada"}), 400
    folder = os.path.abspath(os.path.expanduser(folder))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        return jsonify({"error": f"Não foi possível criar a pasta: {e}"}), 400
    copied, missing = [], []
    for fn in files:
        src = os.path.join(RESULT_FOLDER, os.path.basename(fn))
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(folder, os.path.basename(fn)))
            copied.append(os.path.basename(fn))
        else:
            missing.append(os.path.basename(fn))
    return jsonify({"ok": True, "copied": len(copied), "folder": folder, "missing": missing})


@app.route("/save-edit", methods=["POST"])
def save_edit():
    import base64
    from PIL import ImageFilter
    data     = request.get_json()
    filename = os.path.basename(data.get("filename", ""))
    png_b64  = data.get("data", "")
    scale    = float(data.get("scale", 1.0))
    sharpen  = bool(data.get("sharpen", False))
    upscale  = int(data.get("upscale", 0))   # 0=não, 2=2×, 4=4×
    if not filename or not png_b64:
        return jsonify({"error": "Dados insuficientes"}), 400
    if not filename.lower().endswith(".png"):
        filename += ".png"
    raw = png_b64.split(",", 1)[1] if "," in png_b64 else png_b64
    img = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGBA")

    if sharpen:
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b))
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.2, percent=130, threshold=3))
        img = Image.merge("RGBA", (*rgb.split(), a))

    if scale != 1.0:
        nw = max(1, round(img.width * scale))
        nh = max(1, round(img.height * scale))
        img = img.resize((nw, nh), Image.LANCZOS)

    if upscale > 1:
        img = apply_upscale(img, upscale)

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    out.seek(0)
    with open(os.path.join(RESULT_FOLDER, filename), "wb") as f:
        f.write(out.read())
    return jsonify({"ok": True, "width": img.width, "height": img.height})


@app.route("/download/<filename>")
def download_file(filename):
    path = os.path.join(RESULT_FOLDER, filename)
    if not os.path.exists(path):
        return "Arquivo não encontrado", 404
    return send_file(path, as_attachment=True)


@app.route("/original/<filename>")
def original_file(filename):
    path = os.path.join(ORIGINAL_FOLDER, filename)
    if not os.path.exists(path):
        return "Arquivo não encontrado", 404
    return send_file(path)


@app.route("/result/<filename>")
def result_file(filename):
    path = os.path.join(RESULT_FOLDER, filename)
    if not os.path.exists(path):
        return "Arquivo não encontrado", 404
    return send_file(path)


@app.route("/vectorize", methods=["POST"])
def vectorize():
    import base64, vtracer
    data    = request.get_json()
    png_b64 = data.get("data", "")
    mode    = data.get("mode", "embed")   # "embed" | "trace"
    if not png_b64:
        return jsonify({"error": "Sem dados"}), 400
    raw       = png_b64.split(",", 1)[1] if "," in png_b64 else png_b64
    png_bytes = base64.b64decode(raw)

    if mode == "embed":
        # SVG que embutida o PNG como base64 — qualidade idêntica ao original
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        w, h = img.width, img.height
        b64_data = base64.b64encode(png_bytes).decode()
        svg_str = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
            f'  <image width="{w}" height="{h}" '
            f'xlink:href="data:image/png;base64,{b64_data}"/>\n'
            f'</svg>\n'
        )
    else:
        # Vetorização real — bom para logos/ilustrações, ruim para fotos
        svg_str = vtracer.convert_raw_image_to_svg(
            png_bytes,
            img_format="png",
            colormode="color",
            hierarchical="stacked",
            mode="spline",
            filter_speckle=4,
            color_precision=8,
            layer_difference=8,
            corner_threshold=60,
            length_threshold=4.0,
            max_iterations=10,
            splice_threshold=45,
            path_precision=3,
        )

    buf = io.BytesIO(svg_str.encode())
    buf.seek(0)
    return send_file(buf, mimetype="image/svg+xml",
                     as_attachment=True, download_name="imagem.svg")


@app.route("/download-zip", methods=["POST"])
def download_zip():
    filenames  = request.get_json().get("files", [])
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for fn in filenames:
            p = os.path.join(RESULT_FOLDER, fn)
            if os.path.exists(p):
                zf.write(p, fn)
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype="application/zip",
                     as_attachment=True, download_name="imagens_sem_fundo.zip")


# ── PDF ───────────────────────────────────────────────────────────────────────

@app.route("/pdf/info", methods=["POST"])
def pdf_info():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    pdf_file  = request.files["file"]
    pdf_bytes = pdf_file.read()
    doc       = fitz.open(stream=pdf_bytes, filetype="pdf")
    import base64
    thumbs = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(matrix=fitz.Matrix(0.3, 0.3), alpha=False)
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        thumbs.append({"page": i + 1, "thumb": f"data:image/png;base64,{b64}"})
    doc.close()
    return jsonify({"pages": len(thumbs), "thumbs": thumbs, "pdf_data": pdf_bytes.hex()})


@app.route("/pdf/extract", methods=["POST"])
def pdf_extract():
    data      = request.get_json()
    pages     = data.get("pages", [])
    pdf_hex   = data.get("pdf_data", "")
    dpi       = int(data.get("dpi", 300))
    remove_bg = data.get("remove_bg", True)
    tolerance = int(data.get("threshold", 15))

    if not pdf_hex or not pages:
        return jsonify({"error": "Dados insuficientes"}), 400

    doc   = fitz.open(stream=bytes.fromhex(pdf_hex), filetype="pdf")
    mat   = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    token = uuid.uuid4().hex[:8]  # evita colisão entre extrações de PDFs simultâneas
    results = []

    for page_num in pages:
        try:
            pix = doc[page_num - 1].get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            orig_fn = f"pagina_{page_num:02d}_{token}_original.jpg"
            img.save(os.path.join(ORIGINAL_FOLDER, orig_fn), "JPEG", quality=95)

            out = remove_white_bg(img.convert("RGBA"), tolerance=tolerance) if remove_bg else img.convert("RGBA")
            out_fn = f"pagina_{page_num:02d}_{token}.png"
            out.save(os.path.join(RESULT_FOLDER, out_fn), "PNG")

            results.append({"page": page_num, "result": out_fn, "original": orig_fn, "error": None})
        except Exception as e:
            results.append({"page": page_num, "result": None, "error": str(e)})

    doc.close()
    return jsonify({"results": results})


@app.route("/logo")
def serve_logo():
    path = os.path.join(_BASE_DIR, "icon.png")
    if not os.path.exists(path):
        return "Não encontrado", 404
    return send_file(path, mimetype="image/png")


@app.route("/backgrounds")
def list_backgrounds():
    allowed = {"png", "jpg", "jpeg", "webp"}
    files = sorted(
        f for f in os.listdir(BACKGROUNDS_FOLDER)
        if f.rsplit(".", 1)[-1].lower() in allowed
    )
    return jsonify({"files": files})


@app.route("/bg-img/<filename>")
def bg_image(filename):
    path = os.path.join(BACKGROUNDS_FOLDER, os.path.basename(filename))
    if not os.path.exists(path):
        return "Não encontrado", 404
    return send_file(path)


@app.route("/bg-upload", methods=["POST"])
def bg_upload():
    allowed = {"png", "jpg", "jpeg", "webp"}
    uploaded = []
    for f in request.files.getlist("files"):
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in allowed:
            continue
        safe = os.path.basename(f.filename)
        dest = os.path.join(BACKGROUNDS_FOLDER, safe)
        f.save(dest)
        uploaded.append(safe)
    return jsonify({"ok": True, "files": uploaded})


@app.route("/pick-folder", methods=["POST"])
def pick_folder():
    """Abre dialogo GTK para escolher pasta — funciona apenas com display disponível."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.FileChooserDialog(
            title="Selecionar pasta de destino",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Selecionar", Gtk.ResponseType.OK,
        )
        dialog.set_current_folder(os.path.expanduser("~"))

        response = dialog.run()
        folder = dialog.get_filename() if response == Gtk.ResponseType.OK else ""
        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        return jsonify({"ok": bool(folder), "folder": folder or ""})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Em hospedagem (Railway etc.) a plataforma define PORT e espera bind em 0.0.0.0.
    # Localmente (uso como app de desktop) mantém 127.0.0.1 por padrão.
    _port = int(os.environ.get("PORT", 5050))
    _host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    app.run(debug=False, host=_host, port=_port)
