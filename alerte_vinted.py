#!/usr/bin/env python3
"""Alerte Vinted, version serveur Linux (Ubuntu 22.04 / Python 3.10, sans dependance).

Lit les nouvelles annonces de jeux Switch sur Vinted (une seule requete pour tous les jeux)
et envoie une notification ntfy des qu'une annonce correspond a une ligne de recherches.txt.
Voir LISEZMOI.txt pour l'installation et le lancement.
"""
import argparse
import html
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_RECHERCHES = os.path.join(DOSSIER, "recherches.txt")
FICHIER_LOG = os.path.join(DOSSIER, "alerte.log")
FICHIER_VUS = os.path.join(DOSSIER, "vus.json")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

COULEURS = {"INFO": "37", "GRIS": "90", "CYAN": "36", "JAUNE": "33", "VERT": "1;32", "ROUGE": "31"}

# Le titre ne dit pas tout : pour les annonces qui ont passe tous les filtres (c'est rare),
# on lit la description pour ecarter les boites sans jeu et, sauf --accepter-sans-boite, les jeux sans boite.
PHRASES_BOITE_VIDE = [
    "boite vide", "boitier vide", "boite seule", "boitier seul", "seulement la boite", "juste la boite",
    "uniquement la boite", "que la boite", "sans le jeu", "sans jeu", "pas de jeu", "jeu non inclus", "pas le jeu",
    "empty case", "empty box", "case only", "box only", "no game", "caja vacia", "solo caja", "sin juego", "sin el juego",
    "custodia vuota", "solo custodia", "senza gioco", "leere hulle", "nur hulle", "ohne spiel",
    "apenas caixa", "apenas a caixa", "so a caixa", "so caixa", "caixa vazia", "sem jogo", "sem o jogo",
    "lege doos", "alleen doos", "alleen de doos", "zonder spel",
    # pas un vrai jeu non plus : carte-cle a telecharger, code dans la boite
    "game key card", "game-key card", "game-key-card", "gamekeycard", "key card", "key-card", "keycard",
    "carte cle", "carte-cle", "code in a box", "code in the box", "code in box", "code dans la boite",
    "code de telechargement", "code download", "download code"]
PHRASES_SANS_BOITE = [
    "sans boite", "sans boitier", "sans la boite", "sans le boitier", "cartouche seule", "cartouche uniquement",
    "juste la cartouche", "seulement la cartouche", "que la cartouche", "cartouche nue", "jeu seul", "loose",
    "cartridge only", "no case", "no box", "solo cartucho", "sin caja", "solo cartuccia", "senza custodia",
    "senza scatola", "nur modul", "ohne hulle", "ohne ovp",
    "sem caixa", "apenas cartucho", "apenas o cartucho", "so o cartucho", "zonder doos", "alleen cartridge",
    "losse cartridge"]

GENERIQUES = {"switch", "nintendo", "jeu", "jeux", "game", "games", "video", "the", "les", "des"}


def log(message, couleur="INFO"):
    ligne = "{}  {}".format(datetime.now().strftime("%d/%m %H:%M:%S"), message)
    if sys.stdout.isatty():
        ligne_c = "\033[{}m{}\033[0m".format(COULEURS.get(couleur, "37"), ligne)
        print(ligne_c.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    else:
        print(ligne.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    with open(FICHIER_LOG, "a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def simple(texte):
    """Minuscules sans accents, pour comparer les titres ("Boîte" = "boite")."""
    d = unicodedata.normalize("NFD", str(texte).lower())
    return "".join(c for c in d if unicodedata.category(c) != "Mn")


def mot_present(texte, mot, au_debut=False):
    """Mot ou expression entiere : "vide" trouve "boite vide" mais pas "jeu video"."""
    debut = r"^\W*" if au_debut else r"(?<![a-z0-9])"
    return re.search(debut + re.escape(mot) + r"(?![a-z0-9])", texte) is not None


def lire_recherches():
    recherches = []
    with open(FICHIER_RECHERCHES, encoding="utf-8-sig") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#"):
                continue
            m = [x.strip() for x in ligne.split("|")]
            if len(m) < 2 or not m[1]:
                continue
            try:
                prix = float(m[2].replace(",", ".")) if len(m) >= 3 and m[2] else 0.0
            except ValueError:
                prix = 0.0
            exclus = [simple(x.strip()) for x in m[3].split(",") if x.strip()] if len(m) >= 4 else []
            # le titre devra contenir tous les mots importants ("switch" ne compte pas, "baldur's" -> "baldur")
            mots = [x for x in re.split(r"[^a-z0-9]+", re.sub(r"['’]s\b", "", simple(m[1]))) if x and x not in GENERIQUES]
            cles = [x for x in mots if len(x) >= 3]
            # 5e colonne facultative : titres acceptes (un seul suffit ; "a+b" exige les deux)
            titres = [simple(x.strip()) for x in m[4].split(",") if x.strip()] if len(m) >= 5 else []
            recherches.append({"nom": m[0], "prix_max": prix, "exclus": exclus, "cles": cles, "titres": titres})
    return recherches


def lire_page(url):
    """Renvoie (code HTTP, html). Code 0 = pas de reponse."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def lire_flux(prix_max, page, plateforme):
    """Une seule requete pour tous les jeux : annonces les plus recentes de la categorie Jeux
    (catalog 3026), plateforme Switch, sous le prix max. Renvoie (bloque, code, annonces)."""
    url = "https://www.vinted.fr/catalog?catalog%5B%5D=3026&order=newest_first&page={}".format(page)
    if plateforme:
        url += "&video_game_platform_ids%5B%5D=" + plateforme
    if prix_max > 0:
        url += "&price_to={:g}".format(prix_max)
    code, page_html = lire_page(url)
    if code != 200 or not page_html:
        return True, code, []
    if len(page_html) < 50000 and re.search(r"captcha|datadome|cloudflare|access denied", page_html, re.I):
        return True, code, []

    annonces, ids = [], set()
    for m in re.finditer(r'<a href="/items/(\d+)-[^"]*"[^>]*?\btitle="([^"]*)"', page_html):
        if m.group(1) in ids:
            continue   # une meme annonce apparait parfois deux fois dans la page
        ids.add(m.group(1))
        # titre Vinted : "Nom, Etat: ..., 10.00 €, 11.20 €" (prix, puis prix avec protection acheteur)
        complet = html.unescape(m.group(2))
        nom = re.match(r"^(.*?), (?:État|Marque|Taille)\s*:", complet)
        nom = nom.group(1) if nom else re.split(r", \d", complet)[0]
        p = re.search(r"(\d+(?:[.,]\d+)?)\s*€", complet)
        annonces.append({"id": m.group(1), "titre": nom.strip(),
                         "prix": float(p.group(1).replace(",", ".")) if p else None,
                         "url": "https://www.vinted.fr/items/" + m.group(1)})
    return False, code, annonces


def correspond(rch, a, console):
    """Prix max (hors protection acheteur), titre attendu, aucun mot exclu, console si demandee."""
    if rch["prix_max"] > 0 and a["prix"] is not None and a["prix"] > rch["prix_max"]:
        return False
    titre = simple(a["titre"])
    if rch["titres"]:
        ok = any(all(mot_present(titre, p.strip()) for p in t.split("+") if p.strip()) for t in rch["titres"])
    else:
        ok = all(c in titre for c in rch["cles"])
    if not ok:
        return False
    # un mot exclu qui commence par ^ ne compte qu'en debut de titre ("^boitier")
    if any(mot_present(titre, e.lstrip("^"), au_debut=e.startswith("^")) for e in rch["exclus"]):
        return False
    return not console or mot_present(titre, console)


def raison_description(a, accepter_sans_boite):
    """Lit la description de l'annonce ; renvoie la raison de l'ecarter, ou None."""
    code, page = lire_page(a["url"])
    m = re.search(r'<meta name="description" content="([^"]*)"', page)
    if not m:
        return None   # page illisible : on prefere alerter que rater
    desc = simple(html.unescape(m.group(1)))
    for p in PHRASES_BOITE_VIDE:
        if mot_present(desc, p):
            return 'boite sans le jeu ("{}")'.format(p)
    if not accepter_sans_boite:
        for p in PHRASES_SANS_BOITE:
            if mot_present(desc, p):
                return 'jeu sans boite ("{}")'.format(p)
    return None


def format_prix(prix):
    return "? €" if prix is None else "{:.2f} €".format(prix).replace(".", ",")


def envoyer_alerte(annonces, topic):
    for a in annonces:
        log("*** NOUVELLE : {} - {} -> {} ***".format(format_prix(a["prix"]), a["titre"], a["url"]), "VERT")
    if sys.stdout.isatty():
        print("\a", end="", flush=True)   # bip du terminal, si quelqu'un regarde
    if not topic:
        return
    for a in annonces[:3]:
        req = urllib.request.Request(
            "https://ntfy.sh/" + topic,
            data="{} - {}".format(format_prix(a["prix"]), a["titre"]).encode("utf-8"),
            headers={"Title": "Vinted : nouvelle annonce", "Priority": "urgent",
                     "Tags": "shopping_cart", "Click": a["url"]},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            log("Echec de l'envoi au telephone : {}".format(e), "ROUGE")


def lire_vus():
    try:
        with open(FICHIER_VUS, encoding="utf-8-sig") as f:
            return {k: list(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def ecrire_vus(vus):
    try:
        with open(FICHIER_VUS, "w", encoding="utf-8") as f:
            json.dump(vus, f)
    except OSError:
        pass


def main():
    p = argparse.ArgumentParser(description="Alerte Vinted : nouvelles annonces de jeux Switch (notifications ntfy).")
    p.add_argument("--intervalle", type=int, default=20, help="secondes entre deux verifications (defaut 20)")
    p.add_argument("--plateforme", default="1273", help="filtre Plateforme de Vinted, 1273 = Switch ('' pour toutes)")
    p.add_argument("--console", default="", help="mot obligatoire en plus dans le titre (ex. switch)")
    p.add_argument("--ntfy", default="switch2-zelda-1jw6pdst", help="canal ntfy du telephone ('' pour desactiver)")
    p.add_argument("--accepter-sans-boite", action="store_true", help="accepte aussi les jeux vendus sans boite")
    p.add_argument("--une-fois", action="store_true", help="un seul passage puis arret (pour tester)")
    p.add_argument("--test-alerte", action="store_true", help="envoie une fausse alerte pour tester le telephone")
    args = p.parse_args()

    if args.test_alerte:
        envoyer_alerte([{"titre": "TEST - ceci est un essai", "prix": 5.0, "url": "https://www.vinted.fr/"}], args.ntfy)
        return

    log("Surveillance Vinted lancee : tous tes jeux verifies ensemble toutes les ~{} s. Ctrl+C pour arreter.".format(args.intervalle))
    vus = lire_vus()
    intervalle = args.intervalle
    derniere_ligne = time.time()
    while True:
        debut_tour = time.time()
        recherches = lire_recherches()   # relu a chaque tour : modifiable sans relancer
        if not recherches:
            time.sleep(intervalle)
            continue
        prix_max = max(r["prix_max"] for r in recherches)
        cle = "flux|{:g}|p{}".format(prix_max, args.plateforme)
        premiere = cle not in vus

        # page 1 = les annonces les plus recentes ; si elles sont toutes nouvelles, on lit aussi la page 2
        bloque, code, annonces = lire_flux(prix_max, 1, args.plateforme)
        if not bloque and not premiere and annonces and not any(a["id"] in vus[cle] for a in annonces):
            time.sleep(2)
            b2, _, page2 = lire_flux(prix_max, 2, args.plateforme)
            if not b2:
                deja = {a["id"] for a in annonces}
                annonces += [a for a in page2 if a["id"] not in deja]

        a_alerter = []
        if bloque:
            log("BLOQUE par Vinted (code {})".format(code), "JAUNE")
        else:
            if premiere:
                vus[cle] = []
            # au premier passage on examine tout ce qui est en ligne (une relance ne fait rien rater)
            deja_vus = set(vus[cle])
            a_examiner = annonces if premiere else [a for a in annonces if a["id"] not in deja_vus]
            vus[cle].extend(a["id"] for a in a_examiner)
            vus[cle] = vus[cle][-1000:]
            vus = {cle: vus[cle]}   # on oublie les anciens formats ou un autre prix
            for a in a_examiner:
                rch = next((r for r in recherches if correspond(r, a, args.console)), None)
                if not rch:
                    continue
                raison = raison_description(a, args.accepter_sans_boite)
                if raison:
                    log("{:<24} ecartee, {} : {} {}".format(rch["nom"], raison, format_prix(a["prix"]), a["titre"]), "GRIS")
                else:
                    a_alerter.append(a)
                time.sleep(random.uniform(2, 4))
            ecrire_vus(vus)
            if premiere:
                log("{} annonces Switch a {:g} € max deja en ligne, dont {} correspondant a tes criteres".format(
                    len(annonces), prix_max, len(a_alerter)), "CYAN")
            elif a_examiner or time.time() - derniere_ligne >= 300:
                # une ligne quand il y a du nouveau, sinon une toutes les 5 min pour montrer que ca tourne
                log("Verification : {} nouvelle(s) annonce(s), {} correspondant a tes criteres".format(
                    len(a_examiner), len(a_alerter)), "GRIS")
                derniere_ligne = time.time()
        if a_alerter:
            envoyer_alerte(a_alerter, args.ntfy)
        if args.une_fois:
            break
        if bloque:
            # securite : apres un blocage, on attend 5 min puis on ralentit (jusqu'a 3 min)
            intervalle = min(180, int(intervalle * 1.5))
            log("Pause de 5 min, puis je ralentis : verification toutes les ~{} s".format(intervalle), "JAUNE")
            time.sleep(300)
        else:
            reste = intervalle * random.uniform(0.85, 1.15) - (time.time() - debut_tour)
            if reste > 0:
                time.sleep(reste)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Surveillance arretee.")
