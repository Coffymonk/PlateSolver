# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Installation guides for Windows and macOS, in English and Swedish, as PDF.

    python build/guides/make_guides.py

writes the four PDFs to build/dist/windows and build/dist/macos. The guides contain no version number, so
they only need to be made again when the installation itself changes. Needs reportlab
(pip install reportlab) and the DejaVu fonts (Linux: fonts-dejavu; the script also looks in the usual
Windows and macOS font folders).
"""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[2]
ICON = ROOT / "build" / "icons" / "platesolver.png"
RELEASES = "github.com/Coffymonk/PlateSolver/releases"


def _font_dir() -> Path:
    for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/Library/Fonts",
              os.path.expanduser("~/Library/Fonts"), r"C:\Windows\Fonts",
              os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts")):
        if Path(d, "DejaVuSans.ttf").is_file():
            return Path(d)
    raise SystemExit("The DejaVu fonts were not found (DejaVuSans.ttf). Install them and run again.")


F = _font_dir()
pdfmetrics.registerFont(TTFont("Sans", str(F / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Sans-Bold", str(F / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Sans-Oblique", str(F / "DejaVuSans-Oblique.ttf")))
registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique", boldItalic="Sans-Bold")

NAVY = colors.HexColor("#1b2a41")
ACCENT = colors.HexColor("#3d8ee6")
MUTED = colors.HexColor("#5b6472")
BOX = colors.HexColor("#eef3fa")

base = ParagraphStyle("b", fontName="Sans", fontSize=10, leading=14.2, textColor=colors.HexColor("#1d2128"))
title = ParagraphStyle("t", parent=base, fontName="Sans-Bold", fontSize=21, leading=25, textColor=NAVY)
sub = ParagraphStyle("s", parent=base, fontSize=10.5, textColor=MUTED)
h = ParagraphStyle("h", parent=base, fontName="Sans-Bold", fontSize=12, leading=15, textColor=NAVY, spaceBefore=2)
bullet = ParagraphStyle("bl", parent=base, leftIndent=12, bulletIndent=2)
note = ParagraphStyle("n", parent=base, fontSize=9.2, leading=13, textColor=colors.HexColor("#3a3f47"))
small = ParagraphStyle("sm", parent=base, fontSize=8.5, leading=11, textColor=MUTED)


def step(num, heading, lines):
    badge = Table([[Paragraph(f"<font color='white'><b>{num}</b></font>",
                              ParagraphStyle("x", parent=base, fontSize=12, alignment=1))]],
                  colWidths=[8 * mm], rowHeights=[8 * mm])
    badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ROUNDEDCORNERS", [4, 4, 4, 4])]))
    body = [Paragraph(heading, h)] + [Paragraph(t, bullet, bulletText="•") for t in lines]
    t = Table([[badge, body]], colWidths=[11 * mm, 160 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return KeepTogether([t, Spacer(1, 7)])


def box(paras, bg=BOX):
    t = Table([[paras]], colWidths=[171 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 7),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("ROUNDEDCORNERS", [5, 5, 5, 5])]))
    return t


def build(path, T):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=19 * mm, rightMargin=19 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=T["doc_title"], author="PlateSolver",
                            subject=T["subject"])
    head = Table([[Image(str(ICON), 17 * mm, 17 * mm), [Paragraph(T["title"], title), Paragraph(T["sub"], sub)]]],
                 colWidths=[21 * mm, 150 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story = [head, Spacer(1, 6),
             Table([[""]], colWidths=[171 * mm], rowHeights=[1.2], style=[("BACKGROUND", (0, 0), (-1, -1), ACCENT)]),
             Spacer(1, 8), box([Paragraph(T["intro"], note)]), Spacer(1, 10)]
    for i, (hd, lines) in enumerate(T["steps"], 1):
        story.append(step(i, hd, lines))
    if T.get("alt"):
        story += [box([Paragraph(f"<b>{T['alt_h']}</b> {T['alt']}", note)]), Spacer(1, 8)]
    story += [Spacer(1, 2), box([Paragraph(f"<b>{T['help_h']}</b> {T['help']}", note),
                                 Spacer(1, 4), Paragraph(f"<b>{T['upd_h']}</b> {T['upd']}", note),
                                 Spacer(1, 4), Paragraph(f"<b>{T['rem_h']}</b> {T['rem']}", note)]),
              Spacer(1, 8), Paragraph(T["foot"], small)]

    def page(c, d):
        c.saveState()
        c.setFont("Sans", 8)
        c.setFillColor(MUTED)
        c.drawRightString(A4[0] - 19 * mm, 9 * mm, f"{T['page']} {d.page}")
        c.drawString(19 * mm, 9 * mm, f"PlateSolver · {RELEASES}")
        c.restoreState()
    doc.build(story, onFirstPage=page, onLaterPages=page)


# --------------------------------------------------------------------------- shared steps
ASTAP_EN = ("Install ASTAP – the program that recognises the stars", [
    "The first time PlateSolver starts, it offers to install ASTAP: click <b>Install ASTAP…</b> (it is also "
    "under <b>Tools › Install ASTAP and star databases…</b>).",
    "Read the short note at the top, tick <b>I understand…</b> and click <b>Download and install ASTAP</b>. "
    "Follow ASTAP's installer and keep the suggested folder.",
    "Then, in step 2 of the same window, install the star database marked <b>(recommended)</b>: <b>W08</b> "
    "for phone photos, <b>D50</b> for telescope pictures. It is another installer – just follow it.",
    "Click <b>Check again</b>. Both should now show a green ✓."])
PROFILE_EN = ("Tell PlateSolver about your camera", [
    "Choose a <b>Profile</b> in the toolbar at the top – for example a smartphone profile for phone photos.",
    "For a telescope, use <b>Tools › Profiles…</b> to make your own profile and enter the focal length."])
TRY_EN = ("Try it!", [
    "<b>File › Open</b> a picture of the night sky (JPG, FITS, TIFF and others), then click <b>Solve</b>.",
    "PlateSolver marks the stars and objects on the picture and lists them on the right, with their "
    "distances. Double-click a name to read about it.",
    "Keep the computer online: object names, distances and links come from the internet."])

ASTAP_SV = ("Installera ASTAP – programmet som känner igen stjärnorna", [
    "Första gången PlateSolver startar erbjuder det sig att installera ASTAP: klicka på "
    "<b>Install ASTAP…</b> (finns också under <b>Tools › Install ASTAP and star databases…</b>).",
    "Läs den korta texten överst, kryssa i <b>I understand…</b> och klicka på <b>Download and install "
    "ASTAP</b>. Följ ASTAP:s installationsprogram och behåll den föreslagna mappen.",
    "Installera sedan, i steg 2 i samma fönster, stjärndatabasen som är märkt <b>(recommended)</b>: "
    "<b>W08</b> för mobilbilder, <b>D50</b> för bilder genom teleskop. Det är ett eget installationsprogram "
    "– följ bara stegen.",
    "Klicka på <b>Check again</b>. Båda ska nu visa en grön ✓."])
PROFILE_SV = ("Berätta för PlateSolver vilken kamera du har", [
    "Välj en <b>Profile</b> (profil) i verktygsfältet överst – till exempel en mobilprofil för bilder "
    "tagna med mobiltelefon.",
    "För teleskop: gör en egen profil under <b>Tools › Profiles…</b> och fyll i brännvidden."])
TRY_SV = ("Prova!", [
    "Öppna en bild av natthimlen med <b>File › Open</b> (JPG, FITS, TIFF med flera) och klicka på "
    "<b>Solve</b>.",
    "PlateSolver märker ut stjärnor och objekt i bilden och listar dem till höger, med avstånd. "
    "Dubbelklicka på ett namn för att läsa mer om objektet.",
    "Ha datorn ansluten till internet: namn, avstånd och länkar hämtas därifrån."])

# --------------------------------------------------------------------------- Windows
EN = dict(
    doc_title="PlateSolver – Installation guide (Windows)", subject="How to install and start PlateSolver",
    title="Installing PlateSolver", sub="Windows · step by step · no technical knowledge needed", page="Page",
    intro="PlateSolver recognises the stars in your night-sky photos and shows which stars, galaxies and nebulae "
          f"are in the picture – with their distance from Earth. It is free: download it from <b>{RELEASES}</b>, "
          "or use the file you were sent. Follow these steps once; it takes about 10 minutes plus the download "
          "time. (In the file names below, “…” stands for the version number.)",
    steps=[
        ("Download the installer", [
            f"On <b>{RELEASES}</b>, open the newest release and download <b>PlateSolver-…-setup.exe</b> under "
            "<i>Assets</i>.",
            "Prefer not to install anything? Download <b>PlateSolver-…-windows.zip</b> instead and see "
            "<i>Without installing</i> below."]),
        ("Run the installer", [
            "Double-click <b>PlateSolver-…-setup.exe</b> (you find it in your <b>Downloads</b> folder).",
            "Windows may show a blue warning, <i>“Windows protected your PC”</i>. That only means the program "
            "isn't from a big company. Click <b>More info</b>, then <b>Run anyway</b>.",
            "Choose <b>Install for all users</b> (Windows asks for permission – click <b>Yes</b>) or <b>Install for "
            "me only</b>. Then click <b>Next</b> through the steps; tick <b>Create a desktop shortcut</b> if you "
            "want one, and click <b>Install</b>.",
            "Click <b>Finish</b> – PlateSolver starts. Later you find it in the <b>Start</b> menu."]),
        ASTAP_EN, PROFILE_EN, TRY_EN,
    ],
    alt_h="Without installing:",
    alt="right-click <b>PlateSolver-…-windows.zip</b>, choose <b>Extract All…</b> and move the extracted "
        "<b>PlateSolver</b> folder somewhere permanent, for example <b>Documents</b>. Start "
        "<b>PlateSolver.exe</b> in that folder (keep the <b>_internal</b> folder next to it). Don't start the "
        "program from inside the zip file. Then continue with step 3.",
    help_h="Help:", help="press <b>F1</b> in PlateSolver for the full manual.",
    upd_h="Updating to a new version:", upd="run the new <b>PlateSolver-…-setup.exe</b>; it replaces the old "
          "version. With the zip: delete the old PlateSolver folder and unpack the new one in its place. Your "
          "settings are kept either way.",
    rem_h="Removing PlateSolver:", rem="Windows <b>Settings › Apps › Installed apps</b>, click <b>⋯</b> next to "
          "PlateSolver and choose <b>Uninstall</b> (with the zip: delete the PlateSolver folder). ASTAP is removed "
          "the same way.",
    foot="ASTAP is free software by Han Kleijn (hnsky.org), downloaded from its official site and installed with "
         "its own installer.",
)

SV = dict(
    doc_title="PlateSolver – Installationsguide (Windows)", subject="Så installerar och startar du PlateSolver",
    title="Installera PlateSolver", sub="Windows · steg för steg · inga tekniska förkunskaper behövs", page="Sida",
    intro="PlateSolver känner igen stjärnorna i dina bilder av natthimlen och visar vilka stjärnor, galaxer och "
          "nebulosor som finns i bilden – och hur långt bort de är. Programmet är gratis: ladda ner det från "
          f"<b>{RELEASES}</b>, eller använd filen du har fått. Gör stegen nedan en gång; det tar ungefär 10 "
          "minuter plus tiden för nedladdningarna. (I filnamnen nedan står ”…” för versionsnumret.) Programmets "
          "menyer är på engelska, därför står menynamnen på engelska här.",
    steps=[
        ("Ladda ner installationsprogrammet", [
            f"Öppna den senaste versionen på <b>{RELEASES}</b> och ladda ner <b>PlateSolver-…-setup.exe</b> "
            "under <i>Assets</i>.",
            "Vill du inte installera något? Ladda ner <b>PlateSolver-…-windows.zip</b> i stället och läs "
            "<i>Utan installation</i> längre ner."]),
        ("Kör installationsprogrammet", [
            "Dubbelklicka på <b>PlateSolver-…-setup.exe</b> (den ligger i mappen <b>Hämtade filer</b>).",
            "Windows kan visa en blå varning, <i>”Windows skyddade datorn”</i>. Det betyder bara att programmet "
            "inte kommer från ett stort företag. Klicka på <b>Mer information</b> och sedan <b>Kör ändå</b>.",
            "Välj <b>Install for all users</b> (alla användare – Windows frågar om lov, klicka på <b>Ja</b>) eller "
            "<b>Install for me only</b> (bara du). Klicka sedan på <b>Next</b> genom stegen; kryssa i <b>Create a "
            "desktop shortcut</b> om du vill ha en genväg på skrivbordet, och klicka på <b>Install</b>.",
            "Klicka på <b>Finish</b> – PlateSolver startar. Senare hittar du det på <b>Start</b>-menyn."]),
        ASTAP_SV, PROFILE_SV, TRY_SV,
    ],
    alt_h="Utan installation:",
    alt="högerklicka på <b>PlateSolver-…-windows.zip</b>, välj <b>Extrahera alla…</b> och flytta den uppackade "
        "mappen <b>PlateSolver</b> till ett bestående ställe, till exempel <b>Dokument</b>. Starta "
        "<b>PlateSolver.exe</b> i den mappen (låt mappen <b>_internal</b> ligga kvar bredvid). Starta inte "
        "programmet inifrån zip-filen. Fortsätt sedan med steg 3.",
    help_h="Hjälp:", help="tryck på <b>F1</b> i PlateSolver för hela handboken (på engelska).",
    upd_h="Uppdatera till en ny version:", upd="kör den nya <b>PlateSolver-…-setup.exe</b>; den ersätter den "
          "gamla versionen. Med zip-filen: ta bort den gamla mappen PlateSolver och packa upp den nya på samma "
          "ställe. Dina inställningar finns kvar i båda fallen.",
    rem_h="Ta bort PlateSolver:", rem="Windows <b>Inställningar › Appar › Installerade appar</b>, klicka på "
          "<b>⋯</b> bredvid PlateSolver och välj <b>Avinstallera</b> (med zip-filen: radera mappen PlateSolver). "
          "ASTAP tar du bort på samma sätt.",
    foot="ASTAP är fri programvara av Han Kleijn (hnsky.org). Det laddas ner från ASTAP:s officiella webbplats och "
         "installeras med sitt eget installationsprogram.",
)

# --------------------------------------------------------------------------- macOS
MAC_EN = dict(EN)
MAC_EN.update(
    doc_title="PlateSolver – Installation guide (macOS)",
    sub="Mac · step by step · no technical knowledge needed",
    intro="PlateSolver recognises the stars in your night-sky photos and shows which stars, galaxies and nebulae "
          f"are in the picture – with their distance from Earth. It is free: download it from <b>{RELEASES}</b> "
          "(open the newest release and download <b>PlateSolver-…-macos-….dmg</b> under <i>Assets</i>), or use the "
          "file you were sent. A name ending in <b>arm64</b> is for Macs with an Apple chip (M1 or newer); "
          "<b>x86_64</b> is for older Macs with an Intel processor. Follow these steps once; it takes about "
          "10 minutes plus the download time.",
    steps=[
        ("Open the disk image", [
            "Double-click the <b>.dmg</b> file (you find it in your <b>Downloads</b> folder). A window opens "
            "showing <b>PlateSolver</b> and an <b>Applications</b> folder."]),
        ("Copy PlateSolver to Applications", [
            "Drag the <b>PlateSolver</b> icon onto the <b>Applications</b> folder in the same window.",
            "Then eject the disk image: click the ⏏ next to <b>PlateSolver</b> in the Finder sidebar. "
            "You can delete the .dmg file afterwards."]),
        ("Start PlateSolver the first time", [
            "Open <b>Applications</b> in Finder and double-click <b>PlateSolver</b>.",
            "macOS will probably say it can't check the app for malicious software, because the app isn't "
            "registered with Apple. Click <b>Done</b> (or <b>OK</b>).",
            "Open <b>System Settings › Privacy &amp; Security</b>, scroll down to the message about PlateSolver and "
            "click <b>Open Anyway</b>. Confirm with your password or Touch ID, then click <b>Open</b>.",
            "On older macOS versions you can instead right-click PlateSolver in Applications and choose "
            "<b>Open</b>. You only need to do this once."]),
        ("Keep it in the Dock (optional)", [
            "While PlateSolver is running, right-click its icon in the Dock and choose <b>Options › Keep in "
            "Dock</b>."]),
        ("Install ASTAP – the program that recognises the stars", [
            "The first time PlateSolver starts, it offers to install ASTAP: click <b>Install ASTAP…</b> (it is also "
            "under <b>Tools › Install ASTAP and star databases…</b>).",
            "Read the short note at the top, tick <b>I understand…</b> and click <b>Download and install ASTAP</b>. "
            "ASTAP's installer opens: follow it. If macOS blocks it, use <b>Open Anyway</b> as in step 3.",
            "Then, in step 2 of the same window, install the star database marked <b>(recommended)</b>: <b>W08</b> "
            "for phone photos, <b>D50</b> for telescope pictures.",
            "If PlateSolver warns that installing a database <b>would remove another one</b>, choose "
            "<b>Protect and install</b> and enter your Mac password. Nothing is deleted – it keeps both databases.",
            "Click <b>Check again</b>. Both should now show a green ✓."]),
        PROFILE_EN, TRY_EN,
    ],
    alt="",
    upd="drag the new PlateSolver from the new disk image into Applications and choose <b>Replace</b>. "
        "Your settings are kept.",
    rem="drag PlateSolver from Applications to the Bin. To remove ASTAP as well, drag <b>ASTAP</b> from "
        "Applications to the Bin; its star databases are in the folder /usr/local/opt/astap.",
)

MAC_SV = dict(SV)
MAC_SV.update(
    doc_title="PlateSolver – Installationsguide (macOS)",
    sub="Mac · steg för steg · inga tekniska förkunskaper behövs",
    intro="PlateSolver känner igen stjärnorna i dina bilder av natthimlen och visar vilka stjärnor, galaxer och "
          "nebulosor som finns i bilden – och hur långt bort de är. Programmet är gratis: ladda ner det från "
          f"<b>{RELEASES}</b> (öppna den senaste versionen och ladda ner <b>PlateSolver-…-macos-….dmg</b> under "
          "<i>Assets</i>), eller använd filen du har fått. Slutar namnet på <b>arm64</b> är den för Mac med "
          "Apple-chip (M1 eller nyare); <b>x86_64</b> är för äldre Mac med Intel-processor. Gör stegen nedan en "
          "gång; det tar ungefär 10 minuter plus tiden för nedladdningarna. Programmets menyer är på engelska, "
          "därför står menynamnen på engelska här.",
    steps=[
        ("Öppna skivavbilden", [
            "Dubbelklicka på <b>.dmg</b>-filen (den ligger i mappen <b>Hämtade filer</b>). Ett fönster öppnas "
            "med <b>PlateSolver</b> och en mapp som heter <b>Applications</b> (Program)."]),
        ("Kopiera PlateSolver till Program", [
            "Dra symbolen <b>PlateSolver</b> till mappen <b>Applications</b> i samma fönster.",
            "Mata sedan ut skivavbilden: klicka på ⏏ bredvid <b>PlateSolver</b> i Finders sidofält. "
            ".dmg-filen kan du radera efteråt."]),
        ("Starta PlateSolver första gången", [
            "Öppna <b>Program</b> i Finder och dubbelklicka på <b>PlateSolver</b>.",
            "macOS säger troligen att appen inte kan kontrolleras efter skadlig programvara, eftersom den inte är "
            "registrerad hos Apple. Klicka på <b>Klar</b> (eller <b>OK</b>).",
            "Öppna <b>Systeminställningar › Integritet och säkerhet</b>, rulla ner till meddelandet om PlateSolver "
            "och klicka på <b>Öppna ändå</b>. Bekräfta med ditt lösenord eller Touch ID och klicka på "
            "<b>Öppna</b>.",
            "I äldre macOS-versioner kan du i stället högerklicka på PlateSolver i Program och välja "
            "<b>Öppna</b>. Det behöver du bara göra en gång."]),
        ("Behåll i Dock (valfritt)", [
            "Medan PlateSolver är igång: högerklicka på symbolen i Dock och välj <b>Alternativ › Behåll i "
            "Dock</b>."]),
        ("Installera ASTAP – programmet som känner igen stjärnorna", [
            "Första gången PlateSolver startar erbjuder det sig att installera ASTAP: klicka på "
            "<b>Install ASTAP…</b> (finns också under <b>Tools › Install ASTAP and star databases…</b>).",
            "Läs den korta texten överst, kryssa i <b>I understand…</b> och klicka på <b>Download and install "
            "ASTAP</b>. ASTAP:s installationsprogram öppnas – följ stegen. Om macOS stoppar det, använd "
            "<b>Öppna ändå</b> som i steg 3.",
            "Installera sedan, i steg 2 i samma fönster, stjärndatabasen som är märkt <b>(recommended)</b>: "
            "<b>W08</b> för mobilbilder, <b>D50</b> för bilder genom teleskop.",
            "Om PlateSolver varnar för att en databas <b>skulle ta bort en annan</b>, välj <b>Protect and "
            "install</b> och skriv ditt lösenord. Ingenting raderas – båda databaserna behålls.",
            "Klicka på <b>Check again</b>. Båda ska nu visa en grön ✓."]),
        PROFILE_SV, TRY_SV,
    ],
    alt="",
    upd="dra den nya PlateSolver från den nya skivavbilden till Program och välj <b>Ersätt</b>. Dina "
        "inställningar finns kvar.",
    rem="dra PlateSolver från Program till Papperskorgen. Vill du ta bort ASTAP också, dra <b>ASTAP</b> från "
        "Program till Papperskorgen; stjärndatabaserna ligger i mappen /usr/local/opt/astap.",
)


def main() -> None:
    win = ROOT / "build" / "dist" / "windows"
    mac = ROOT / "build" / "dist" / "macos"
    win.mkdir(parents=True, exist_ok=True)
    mac.mkdir(parents=True, exist_ok=True)
    build(win / "PlateSolver - Installation guide Windows (English).pdf", EN)
    build(win / "PlateSolver - Installationsguide Windows (svenska).pdf", SV)
    build(mac / "PlateSolver - Installation guide macOS (English).pdf", MAC_EN)
    build(mac / "PlateSolver - Installationsguide macOS (svenska).pdf", MAC_SV)
    print(f"Four installation guides written to {win} and {mac}")


if __name__ == "__main__":
    main()
