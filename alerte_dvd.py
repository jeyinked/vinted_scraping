#!/usr/bin/env python3
"""Alerte Vinted DVD / Blu-ray : nouvelles annonces de films d'horreur pas chers.

A chaque verification, deux requetes Vinted : la categorie DVD sous le prix max DVD, et les
categories Blu-ray + Blu-ray 4K sous le prix max Blu-ray. Les annonces sont triees avec films.txt
et une notification ntfy part des qu'une annonce correspond. Python 3.10 (Ubuntu 22.04), sans dependance.
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
FICHIER_FILMS = os.path.join(DOSSIER, "films.txt")
FICHIER_LOG = os.path.join(DOSSIER, "alerte.log")
FICHIER_VUS = os.path.join(DOSSIER, "vus.json")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Categories Vinted : 3045 = DVD, 3044 = Blu-ray, 3042 = Blu-ray 4K
FORMATS = [("DVD", ["3045"], "prix_dvd"), ("Blu-ray", ["3044", "3042"], "prix_bluray")]

COULEURS = {"INFO": "37", "GRIS": "90", "CYAN": "36", "JAUNE": "33", "VERT": "1;32", "ROUGE": "31"}

# Pour les annonces qui ont passe les filtres, on lit la description : boitier sans disque = ecarte
PHRASES_SANS_DISQUE = [
    "boitier vide", "boite vide", "sans le dvd", "sans dvd", "sans le disque", "sans disque", "sans blu-ray",
    "sans le blu-ray", "disque manquant", "dvd manquant", "jaquette seule", "juste la jaquette", "boitier seul",
    "seulement le boitier", "uniquement le boitier", "empty case", "case only", "no disc", "disc missing",
    "caja vacia", "sin disco", "custodia vuota", "senza disco", "leere hulle", "ohne disc", "apenas caixa"]


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
    """Minuscules sans accents : "Tronçonneuse" = "tronconneuse"."""
    d = unicodedata.normalize("NFD", str(texte).lower().replace("\u2019", "'").replace("\u2018", "'"))
    return "".join(c for c in d if unicodedata.category(c) != "Mn")


def mot_present(texte, mot, au_debut=False):
    """Mot ou expression entiere : "alien" trouve "Alien, le 8e passager" mais pas "aliens"."""
    debut = r"^\W*" if au_debut else r"(?<![a-z0-9])"
    return re.search(debut + re.escape(mot) + r"(?![a-z0-9])", texte) is not None


def liste(colonne):
    return [simple(x.strip()) for x in colonne.split(",") if x.strip()]


def lire_films():
    """films.txt : "Nom | titres acceptes | mots exclus en plus". La ligne "* | | ..." donne les
    mots exclus pour tous les films."""
    films, exclus_communs = [], []
    with open(FICHIER_FILMS, encoding="utf-8-sig") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#"):
                continue
            m = [x.strip() for x in ligne.split("|")] + ["", ""]
            if m[0] == "*":
                exclus_communs += liste(m[2])
            elif m[1]:
                films.append({"nom": m[0], "titres": liste(m[1]), "exclus": liste(m[2])})
    for film in films:
        film["exclus"] += exclus_communs
    return films


def lire_page(url):
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


def lire_flux(categories, prix_max, page):
    """Annonces les plus recentes des categories donnees, sous le prix max. -> (bloque, code, annonces)"""
    url = "https://www.vinted.fr/catalog?order=newest_first&page={}".format(page)
    url += "".join("&catalog%5B%5D=" + c for c in categories)
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
            continue
        ids.add(m.group(1))
        # titre Vinted : "Nom, Etat: ..., 2.00 €, 2.80 €" (prix, puis prix avec protection acheteur)
        complet = html.unescape(m.group(2))
        nom = re.match(r"^(.*?), (?:État|Marque|Taille)\s*:", complet)
        nom = nom.group(1) if nom else re.split(r", \d", complet)[0]
        p = re.search(r"(\d+(?:[.,]\d+)?)\s*€", complet)
        annonces.append({"id": m.group(1), "titre": nom.strip(),
                         "prix": float(p.group(1).replace(",", ".")) if p else None,
                         "url": "https://www.vinted.fr/items/" + m.group(1)})
    return False, code, annonces


def film_trouve(films, a, prix_max):
    """Le film de films.txt qui correspond a l'annonce, ou None."""
    if prix_max > 0 and a["prix"] is not None and a["prix"] > prix_max:
        return None
    titre = simple(a["titre"])
    for film in films:
        # titres acceptes : un seul suffit ; "a+b" exige les deux ; "^a" = en debut de titre
        if not any(all(mot_present(titre, p.strip().lstrip("^"), au_debut=p.strip().startswith("^"))
                       for p in t.split("+") if p.strip()) for t in film["titres"]):
            continue
        # un mot exclu qui commence par ^ ne compte qu'en debut de titre
        if any(mot_present(titre, e.lstrip("^"), au_debut=e.startswith("^")) for e in film["exclus"]):
            continue
        return film
    return None


def raison_description(a):
    code, page = lire_page(a["url"])
    m = re.search(r'<meta name="description" content="([^"]*)"', page)
    if not m:
        return None   # page illisible : on prefere alerter que rater
    desc = simple(html.unescape(m.group(1)))
    for p in PHRASES_SANS_DISQUE:
        if mot_present(desc, p):
            return 'sans le disque ("{}")'.format(p)
    return None


def format_prix(prix):
    return "? €" if prix is None else "{:.2f} €".format(prix).replace(".", ",")


def envoyer_alerte(annonces, topic):
    for a in annonces:
        log("*** {} {} - {} ({}) -> {} ***".format(a["format"], format_prix(a["prix"]), a["titre"], a["film"], a["url"]), "VERT")
    if sys.stdout.isatty():
        print("\a", end="", flush=True)
    if not topic:
        return
    for a in annonces[:5]:
        req = urllib.request.Request(
            "https://ntfy.sh/" + topic,
            data="{} {} - {}".format(a["format"], format_prix(a["prix"]), a["titre"]).encode("utf-8"),
            headers={"Title": "Horreur : nouvelle annonce", "Priority": "high",
                     "Tags": "jack_o_lantern", "Click": a["url"]},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            log("Echec de l'envoi au telephone : {}".format(e), "ROUGE")


def lire_vus():
    try:
        with open(FICHIER_VUS, encoding="utf-8") as f:
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
    p = argparse.ArgumentParser(description="Alerte Vinted : DVD et Blu-ray de films d'horreur (notifications ntfy).")
    p.add_argument("--prix-dvd", type=float, default=4, help="prix max d'un DVD (defaut 4)")
    p.add_argument("--prix-bluray", type=float, default=10, help="prix max d'un Blu-ray ou Blu-ray 4K (defaut 10)")
    p.add_argument("--intervalle", type=int, default=30, help="secondes entre deux verifications (defaut 30)")
    p.add_argument("--ntfy", default="switch2-zelda-1jw6pdst", help="canal ntfy du telephone ('' pour desactiver)")
    p.add_argument("--une-fois", action="store_true", help="un seul passage puis arret (pour tester)")
    p.add_argument("--test-alerte", action="store_true", help="envoie une fausse alerte pour tester le telephone")
    args = p.parse_args()
    prix = {"prix_dvd": args.prix_dvd, "prix_bluray": args.prix_bluray}

    if args.test_alerte:
        envoyer_alerte([{"format": "DVD", "prix": 1.0, "titre": "TEST - ceci est un essai", "film": "test",
                         "url": "https://www.vinted.fr/"}], args.ntfy)
        return

    log("Surveillance lancee : DVD a {:g} € max, Blu-ray a {:g} € max, toutes les ~{} s. Ctrl+C pour arreter.".format(
        args.prix_dvd, args.prix_bluray, args.intervalle))
    vus = lire_vus()
    intervalle = args.intervalle
    derniere_ligne = time.time()
    while True:
        debut_tour = time.time()
        films = lire_films()   # relu a chaque tour : modifiable sans relancer
        a_alerter, bloque, cles_tour = [], False, []
        for nom_format, categories, cle_prix in FORMATS:
            prix_max = prix[cle_prix]
            cle = "{}|{:g}".format(nom_format, prix_max)
            cles_tour.append(cle)
            premiere = cle not in vus
            b, code, annonces = lire_flux(categories, prix_max, 1)
            if not b and not premiere and annonces and not any(a["id"] in vus[cle] for a in annonces):
                time.sleep(2)   # toute la page est nouvelle : on lit aussi la page 2
                b2, _, page2 = lire_flux(categories, prix_max, 2)
                if not b2:
                    deja = {a["id"] for a in annonces}
                    annonces += [a for a in page2 if a["id"] not in deja]
            if b:
                log("{} : BLOQUE par Vinted (code {})".format(nom_format, code), "JAUNE")
                bloque = True
                break
            if premiere:
                vus[cle] = []
            deja_vus = set(vus[cle])
            # au premier passage on examine tout ce qui est en ligne (une relance ne fait rien rater)
            a_examiner = annonces if premiere else [a for a in annonces if a["id"] not in deja_vus]
            vus[cle] = (vus[cle] + [a["id"] for a in a_examiner])[-1000:]
            trouves = 0
            for a in a_examiner:
                film = film_trouve(films, a, prix_max)
                if not film:
                    continue
                raison = raison_description(a)
                if raison:
                    log("{:<8} ecartee, {} : {} {}".format(nom_format, raison, format_prix(a["prix"]), a["titre"]), "GRIS")
                else:
                    a.update(format=nom_format, film=film["nom"])
                    a_alerter.append(a)
                    trouves += 1
                time.sleep(random.uniform(2, 4))
            if premiere:
                log("{} : {} annonces a {:g} € max deja en ligne, dont {} films d'horreur de ta liste".format(
                    nom_format, len(annonces), prix_max, trouves), "CYAN")
            elif a_examiner or time.time() - derniere_ligne >= 300:
                log("{} : {} nouvelle(s) annonce(s), {} de ta liste".format(nom_format, len(a_examiner), trouves), "GRIS")
            time.sleep(random.uniform(2, 4))
        if not bloque:
            vus = {k: vus[k] for k in cles_tour if k in vus}   # on oublie les anciens prix
            ecrire_vus(vus)
            if time.time() - derniere_ligne >= 300:
                derniere_ligne = time.time()
        if a_alerter:
            envoyer_alerte(a_alerter, args.ntfy)
        if args.une_fois:
            break
        if bloque:
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
