"""Saving protected images, with machine-readable opt-out metadata.

Metadata is not protection - any scraper can strip it in one line - but it is
the part that has legal and policy weight. The IPTC/PLUS "Data Mining"
property is the actual published standard for "do not train on this", honoured
by a growing number of crawlers and cited in licensing disputes. It costs
nothing to embed, so it goes in by default.

Deliberate omission: by default we do *not* write a tag saying "this image was
protected by Mistcoat". A marker like that is a gift to anyone building a
filter to exclude protected images from their dataset. It is available as an
option for artists who want the provenance on record.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .imaging import LoadedImage, to_pil

PLUS_PROHIBIT_AI = "http://ns.useplus.org/ldf/vocab/DMI-PROHIBITED-AIMLTRAINING"

_FORMAT_FOR_SUFFIX = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG",
    ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP",
}
_SUFFIX_FOR_FORMAT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "TIFF": ".tif"}

# Formats we can attach an XMP packet to.
_XMP_CAPABLE = {"JPEG", "WEBP", "PNG"}


@dataclass
class MetadataOptions:
    write_optout: bool = True
    credit: str = ""
    copyright_notice: str = ""
    web_statement: str = ""
    # See module docstring - off by default on purpose.
    declare_protection: bool = False
    tool_version: str = ""


def build_xmp(opts: MetadataOptions) -> str:
    rights = []
    if opts.copyright_notice:
        rights.append(
            "   <dc:rights><rdf:Alt><rdf:li xml:lang='x-default'>"
            f"{escape(opts.copyright_notice)}</rdf:li></rdf:Alt></dc:rights>")
    if opts.credit:
        rights.append(
            "   <dc:creator><rdf:Seq><rdf:li>"
            f"{escape(opts.credit)}</rdf:li></rdf:Seq></dc:creator>")
        rights.append(f"   <photoshop:Credit>{escape(opts.credit)}</photoshop:Credit>")

    mining = ""
    if opts.write_optout:
        terms = ("This image may not be used to train, fine-tune or develop any "
                 "machine learning or generative AI model, or as input to any "
                 "such model, without the express written permission of the "
                 "copyright holder.")
        mining = (
            f"   <plus:DataMining>{PLUS_PROHIBIT_AI}</plus:DataMining>\n"
            "   <xmpRights:Marked>True</xmpRights:Marked>\n"
            "   <xmpRights:UsageTerms><rdf:Alt><rdf:li xml:lang='x-default'>"
            f"{escape(terms)}</rdf:li></rdf:Alt></xmpRights:UsageTerms>\n"
            "   <mistcoat:Robots>noai, noimageai, noml</mistcoat:Robots>")
        if opts.web_statement:
            mining += ("\n   <xmpRights:WebStatement>"
                       f"{escape(opts.web_statement)}</xmpRights:WebStatement>")

    declared = ""
    if opts.declare_protection:
        declared = (f"\n   <mistcoat:Protected>True</mistcoat:Protected>"
                    f"\n   <mistcoat:Version>{escape(opts.tool_version)}</mistcoat:Version>")

    body = "\n".join(x for x in (mining, *rights) if x)
    return (
        "<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>\n"
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'>\n"
        " <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
        "  <rdf:Description rdf:about=''\n"
        "    xmlns:dc='http://purl.org/dc/elements/1.1/'\n"
        "    xmlns:xmpRights='http://ns.adobe.com/xap/1.0/rights/'\n"
        "    xmlns:photoshop='http://ns.adobe.com/photoshop/1.0/'\n"
        "    xmlns:plus='http://ns.useplus.org/ldf/xmp/1.0/'\n"
        "    xmlns:mistcoat='https://mistcoat.app/ns/1.0/'>\n"
        f"{body}{declared}\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        "<?xpacket end='w'?>")


def _exif_for(src: LoadedImage, opts: MetadataOptions) -> Image.Exif:
    exif = Image.Exif()
    if src.exif:
        try:
            exif.load(src.exif)
        except Exception:
            exif = Image.Exif()
    if opts.copyright_notice:
        exif[0x8298] = opts.copyright_notice      # Copyright
    if opts.credit:
        exif[0x013B] = opts.credit                # Artist
    if opts.write_optout:
        note = exif.get(0x9286) or ""             # UserComment
        tag = "AI training prohibited (IPTC DMI-PROHIBITED-AIMLTRAINING)."
        if tag not in str(note):
            exif[0x9286] = (str(note) + " " + tag).strip()
    return exif


def target_path(src: Path, out_dir: Path, suffix: str, fmt: str) -> Path:
    if fmt == "source":
        ext = src.suffix if src.suffix.lower() in _FORMAT_FOR_SUFFIX else ".png"
    else:
        ext = _SUFFIX_FOR_FORMAT.get(fmt.upper(), ".png")
    return out_dir / f"{src.stem}{suffix}{ext}"


def format_for_suffix(suffix: str) -> str | None:
    """"png", "jpeg", "webp", "tiff" for a file extension, else None."""
    fmt = _FORMAT_FOR_SUFFIX.get(suffix.lower())
    return fmt.lower() if fmt else None


def resolve_format(src: LoadedImage, requested: str) -> str:
    if requested and requested != "source":
        return requested.upper()
    fmt = (src.source_format or "").upper()
    if fmt in ("JPEG", "PNG", "WEBP", "TIFF"):
        return fmt
    if src.path is not None:
        return _FORMAT_FOR_SUFFIX.get(src.path.suffix.lower(), "PNG")
    return "PNG"


def save_image(src: LoadedImage, rgb, dest: Path, fmt: str,
               opts: MetadataOptions, jpeg_quality: int = 95) -> Path:
    """Write the protected pixels to ``dest``, carrying colour and metadata over."""
    fmt = fmt.upper()
    im = to_pil(src, rgb)
    if fmt == "JPEG" and im.mode == "RGBA":
        # JPEG has no alpha; composite onto white rather than failing.
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg

    params: dict = {}
    if src.icc_profile:
        params["icc_profile"] = src.icc_profile

    exif = _exif_for(src, opts)
    xmp = build_xmp(opts) if (opts.write_optout or opts.credit
                              or opts.copyright_notice) else ""

    if fmt == "JPEG":
        params.update(quality=jpeg_quality, subsampling=0, optimize=True,
                      exif=exif.tobytes())
        if xmp:
            params["xmp"] = xmp.encode("utf-8")
    elif fmt == "WEBP":
        params.update(quality=max(jpeg_quality, 90), method=5,
                      exif=exif.tobytes())
        if xmp:
            params["xmp"] = xmp.encode("utf-8")
    elif fmt == "PNG":
        params.update(compress_level=6)
        info = PngInfo()
        if xmp:
            info.add_itxt("XML:com.adobe.xmp", xmp, zip=False)
        params["pnginfo"] = info
        params["exif"] = exif.tobytes()
    elif fmt == "TIFF":
        params.update(compression="tiff_lzw", exif=exif.tobytes())

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        im.save(dest, format=fmt, **params)
    except (OSError, ValueError, TypeError):
        # Some Pillow builds reject xmp/exif for a given format. Retry bare
        # rather than lose the artwork.
        for key in ("xmp", "exif", "pnginfo"):
            params.pop(key, None)
        im.save(dest, format=fmt, **params)
    return dest
