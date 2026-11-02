#!/usr/bin/env python3
"""Alerte Vinted DVD / Blu-ray : nouvelles annonces de films d'horreur pas chers.

A chaque verification, deux requetes Vinted : la categorie DVD sous le prix max DVD, et les
categories Blu-ray + Blu-ray 4K sous le prix max Blu-ray. Les annonces sont triees avec films.txt
et une notification ntfy part des qu'une annonce correspond. Python 3.10 (Ubuntu 22.04), sans dependance.
"""
# =====================================================================================
#  EN RESUME, CE QUE FAIT CE PROGRAMME, EN BOUCLE, TOUTES LES ~30 SECONDES :
#    1. il relit ta liste de films (films.txt) ;
#    2. pour chaque format (DVD, puis Blu-ray + 4K), il telecharge la page Vinted
#       "les plus recents de cette categorie, sous le prix max" ;
#    3. il en extrait chaque annonce : numero, titre, prix, lien ;
#    4. il ne garde que les annonces jamais vues (memorisees dans vus.json) ;
#    5. pour chacune, il cherche si elle correspond a un film de ta liste (titres acceptes, mots exclus) ;
#    6. si oui, il lit la description pour ecarter les boitiers vendus sans disque ;
#    7. il envoie les annonces retenues sur ton telephone via ntfy ;
#    8. il attend, puis recommence.
# =====================================================================================

# --- Les "boites a outils" de Python dont on a besoin (toutes fournies avec Python) ---
import argparse            # lire les options de la ligne de commande (--prix-dvd, --ntfy...)
import html                # transformer "&amp;" en "&", "&#x27;" en "'" etc. dans les pages web
import json                # lire et ecrire le fichier vus.json
import os                  # manipuler les chemins de fichiers
import random              # tirer des durees d'attente au hasard (pour ne pas etre trop regulier)
import re                  # "expressions regulieres" : chercher des motifs dans du texte
import sys                 # savoir si on ecrit dans un vrai terminal, etc.
import time                # attendre (sleep) et mesurer le temps
import unicodedata         # enlever les accents ("é" -> "e")
import urllib.error        # gerer les erreurs de telechargement
import urllib.request      # telecharger une page web / envoyer une notification
from datetime import datetime   # afficher la date et l'heure dans le journal

# --- Les fichiers utilises, tous places dans le meme dossier que ce script ---
DOSSIER = os.path.dirname(os.path.abspath(__file__))       # le dossier du script
FICHIER_FILMS = os.path.join(DOSSIER, "films.txt")          # ta liste de films
FICHIER_LOG = os.path.join(DOSSIER, "alerte.log")           # le journal (historique)
FICHIER_VUS = os.path.join(DOSSIER, "vus.json")             # les annonces deja vues

# Le "User-Agent" est la carte de visite qu'un navigateur donne aux sites.
# On se presente comme un navigateur Chrome ordinaire, sinon Vinted refuse de repondre.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Categories Vinted : 3045 = DVD, 3044 = Blu-ray, 3042 = Blu-ray 4K
# Chaque format = (nom affiche, numeros de categories Vinted, nom du reglage de prix max)
FORMATS = [("DVD", ["3045"], "prix_dvd"), ("Blu-ray", ["3044", "3042"], "prix_bluray")]

# Codes de couleur du terminal (37 = blanc, 90 = gris, 32 = vert...) pour un journal lisible
COULEURS = {"INFO": "37", "GRIS": "90", "CYAN": "36", "JAUNE": "33", "VERT": "1;32", "ROUGE": "31"}

# Pour les annonces qui ont passe les filtres, on lit la description : boitier sans disque = ecarte
# Si une de ces expressions apparait dans la description, l'annonce est ecartee.
PHRASES_SANS_DISQUE = [
    "boitier vide", "boite vide", "sans le dvd", "sans dvd", "sans le disque", "sans disque", "sans blu-ray",
    "sans le blu-ray", "disque manquant", "dvd manquant", "jaquette seule", "juste la jaquette", "boitier seul",
    "seulement le boitier", "uniquement le boitier", "empty case", "case only", "no disc", "disc missing",
    "caja vacia", "sin disco", "custodia vuota", "senza disco", "leere hulle", "ohne disc", "apenas caixa"]


def log(message, couleur="INFO"):
    """Ecrit un message, precede de la date et l'heure, a l'ecran ET dans alerte.log."""
    ligne = "{}  {}".format(datetime.now().strftime("%d/%m %H:%M:%S"), message)
    if sys.stdout.isatty():
        # Vrai terminal : on ajoute la couleur. "errors=replace" evite un plantage sur un emoji.
        ligne_c = "\033[{}m{}\033[0m".format(COULEURS.get(couleur, "37"), ligne)
        print(ligne_c.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    else:
        # Sortie redirigee (fichier, service...) : pas de couleur
        print(ligne.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    with open(FICHIER_LOG, "a", encoding="utf-8") as f:     # "a" = ajouter a la fin sans effacer
        f.write(ligne + "\n")


def simple(texte):
    """Minuscules sans accents : "Tronçonneuse" = "tronconneuse"."""
    # On remplace aussi les apostrophes typographiques (’ ‘) par une apostrophe simple ('),
    # puis on separe chaque lettre de son accent (NFD) et on garde tout sauf les accents.
    d = unicodedata.normalize("NFD", str(texte).lower().replace("’", "'").replace("‘", "'"))
    return "".join(c for c in d if unicodedata.category(c) != "Mn")


def mot_present(texte, mot, au_debut=False):
    """Mot ou expression entiere : "alien" trouve "Alien, le 8e passager" mais pas "aliens"."""
    # (?<![a-z0-9]) = pas de lettre juste avant ; (?![a-z0-9]) = pas de lettre juste apres.
    # Avec au_debut=True (titres ou mots en "^"), le mot doit etre au debut du titre.
    debut = r"^\W*" if au_debut else r"(?<![a-z0-9])"
    return re.search(debut + re.escape(mot) + r"(?![a-z0-9])", texte) is not None


def liste(colonne):
    """"Halloween, Michael Myers" -> ["halloween", "michael myers"] (decoupe aux virgules, sans accents)."""
    return [simple(x.strip()) for x in colonne.split(",") if x.strip()]


def lire_films():
    """films.txt : "Nom | titres acceptes | mots exclus en plus". La ligne "* | | ..." donne les
    mots exclus pour tous les films."""
    films, exclus_communs = [], []
    with open(FICHIER_FILMS, encoding="utf-8-sig") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#"):
                continue                                    # ligne vide ou commentaire : on passe
            # On coupe a chaque "|" ; c'est la POSITION qui donne le sens :
            #   m[0] = nom, m[1] = titres acceptes, m[2] = mots exclus
            # (on ajoute deux cases vides pour les lignes ou la 3e colonne manque)
            m = [x.strip() for x in ligne.split("|")] + ["", ""]
            if m[0] == "*":
                exclus_communs += liste(m[2])               # ligne "*" : exclusions communes a tous
            elif m[1]:
                films.append({"nom": m[0], "titres": liste(m[1]), "exclus": liste(m[2])})
    # Chaque film recoit en plus les exclusions communes
    for film in films:
        film["exclus"] += exclus_communs
    return films


def lire_page(url):
    """Telecharge une page web. Renvoie (code HTTP, contenu). Code 0 = pas de reponse."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,                    # on se presente comme un navigateur
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9",         # on demande la page en francais
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:          # 30 s maximum d'attente
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""                                           # le site a repondu "non" (403, 429...)
    except Exception:
        return 0, ""                                                # pas de reseau, delai depasse...


def lire_flux(categories, prix_max, page):
    """Annonces les plus recentes des categories donnees, sous le prix max. -> (bloque, code, annonces)"""
    # On construit l'adresse de recherche Vinted, comme si on reglait les filtres sur le site :
    #   order=newest_first   -> les plus recentes d'abord
    #   catalog[]=3045 ...   -> la ou les categories (DVD, ou Blu-ray + Blu-ray 4K)
    #   price_to=4           -> prix maximum
    # (%5B%5D est la facon d'ecrire "[]" dans une adresse web)
    url = "https://www.vinted.fr/catalog?order=newest_first&page={}".format(page)
    url += "".join("&catalog%5B%5D=" + c for c in categories)
    if prix_max > 0:
        url += "&price_to={:g}".format(prix_max)
    code, page_html = lire_page(url)
    # Si Vinted n'a pas repondu "OK", on considere qu'on est bloque
    if code != 200 or not page_html:
        return True, code, []
    # Une page tres courte qui parle de captcha = page anti-robot, pas la vraie page
    if len(page_html) < 50000 and re.search(r"captcha|datadome|cloudflare|access denied", page_html, re.I):
        return True, code, []
    # Dans la page, chaque annonce est un lien du type :
    #   <a href="/items/10628081561-dvd-halloween" ... title="Dvd Halloween, État: ..., 1.20 €, 1.98 €">
    # On recupere le numero (groupe 1) et le titre complet (groupe 2) de chaque annonce.
    annonces, ids = [], set()
    for m in re.finditer(r'<a href="/items/(\d+)-[^"]*"[^>]*?\btitle="([^"]*)"', page_html):
        if m.group(1) in ids:
            continue                               # une meme annonce apparait parfois deux fois
        ids.add(m.group(1))
        # titre Vinted : "Nom, Etat: ..., 2.00 €, 2.80 €" (prix, puis prix avec protection acheteur)
        complet = html.unescape(m.group(2))
        # Le nom = tout ce qui est avant ", État:" (ou ", Marque:", ", Taille:")
        nom = re.match(r"^(.*?), (?:État|Marque|Taille)\s*:", complet)
        nom = nom.group(1) if nom else re.split(r", \d", complet)[0]
        # Le prix = le premier "nombre suivi de €"
        p = re.search(r"(\d+(?:[.,]\d+)?)\s*€", complet)
        annonces.append({"id": m.group(1), "titre": nom.strip(),
                         "prix": float(p.group(1).replace(",", ".")) if p else None,
                         "url": "https://www.vinted.fr/items/" + m.group(1)})
    return False, code, annonces


def film_trouve(films, a, prix_max):
    """Le film de films.txt qui correspond a l'annonce, ou None."""
    # Trop cher pour ce format -> non
    if prix_max > 0 and a["prix"] is not None and a["prix"] > prix_max:
        return None
    titre = simple(a["titre"])
    for film in films:                    # on essaie chaque film de la liste, dans l'ordre
        # titres acceptes : un seul suffit ; "a+b" exige les deux ; "^a" = en debut de titre
        if not any(all(mot_present(titre, p.strip().lstrip("^"), au_debut=p.strip().startswith("^"))
                       for p in t.split("+") if p.strip()) for t in film["titres"]):
            continue                      # le titre ne parle pas de ce film : film suivant
        # un mot exclu qui commence par ^ ne compte qu'en debut de titre
        if any(mot_present(titre, e.lstrip("^"), au_debut=e.startswith("^")) for e in film["exclus"]):
            continue                      # un mot exclu est present : film suivant
        return film                       # trouve !
    return None                           # aucun film ne correspond


def raison_description(a):
    """Lit la description de l'annonce ; renvoie la raison de l'ecarter, ou None."""
    # Une requete de plus, mais seulement pour les annonces qui ont deja passe les filtres
    code, page = lire_page(a["url"])
    # La description est dans une balise <meta name="description" content="...">
    m = re.search(r'<meta name="description" content="([^"]*)"', page)
    if not m:
        return None   # page illisible : on prefere alerter que rater
    desc = simple(html.unescape(m.group(1)))
    for p in PHRASES_SANS_DISQUE:
        if mot_present(desc, p):
            return 'sans le disque ("{}")'.format(p)
    return None       # rien de suspect : l'annonce est bonne


def format_prix(prix):
    """1.2 -> "1,20 €" (format francais)."""
    return "? €" if prix is None else "{:.2f} €".format(prix).replace(".", ",")


def envoyer_alerte(annonces, topic):
    """Ecrit les annonces dans le journal et les envoie sur le telephone via ntfy."""
    for a in annonces:
        log("*** {} {} - {} ({}) -> {} ***".format(a["format"], format_prix(a["prix"]), a["titre"], a["film"], a["url"]), "VERT")
    if sys.stdout.isatty():
        print("\a", end="", flush=True)   # bip du terminal, si quelqu'un regarde
    if not topic:
        return                            # pas de canal ntfy : on s'arrete la
    # ntfy : on "poste" un message a l'adresse https://ntfy.sh/<nom-du-canal>.
    # Tous les telephones abonnes a ce nom dans l'appli ntfy recoivent la notification.
    for a in annonces[:5]:                # au plus 5 notifications d'un coup
        req = urllib.request.Request(
            "https://ntfy.sh/" + topic,
            data="{} {} - {}".format(a["format"], format_prix(a["prix"]), a["titre"]).encode("utf-8"),   # le texte
            headers={"Title": "Horreur : nouvelle annonce",   # le titre de la notification
                     "Priority": "high",                       # priorite haute (un cran sous "urgent")
                     "Tags": "jack_o_lantern",                 # l'icone (une citrouille)
                     "Click": a["url"]},                       # toucher la notif ouvre l'annonce
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            log("Echec de l'envoi au telephone : {}".format(e), "ROUGE")


def lire_vus():
    """Charge vus.json : les numeros d'annonces deja vues, par format (vide au tout premier lancement)."""
    try:
        with open(FICHIER_VUS, encoding="utf-8") as f:
            return {k: list(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def ecrire_vus(vus):
    """Enregistre les annonces deja vues, pour ne pas re-alerter apres une relance."""
    try:
        with open(FICHIER_VUS, "w", encoding="utf-8") as f:
            json.dump(vus, f)
    except OSError:
        pass


def main():
    # --- 1. Les options de la ligne de commande et leurs valeurs par defaut ---
    p = argparse.ArgumentParser(description="Alerte Vinted : DVD et Blu-ray de films d'horreur (notifications ntfy).")
    p.add_argument("--prix-dvd", type=float, default=4, help="prix max d'un DVD (defaut 4)")
    p.add_argument("--prix-bluray", type=float, default=10, help="prix max d'un Blu-ray ou Blu-ray 4K (defaut 10)")
    p.add_argument("--intervalle", type=int, default=30, help="secondes entre deux verifications (defaut 30)")
    p.add_argument("--ntfy", default="switch2-zelda-1jw6pdst", help="canal ntfy du telephone ('' pour desactiver)")
    p.add_argument("--une-fois", action="store_true", help="un seul passage puis arret (pour tester)")
    p.add_argument("--test-alerte", action="store_true", help="envoie une fausse alerte pour tester le telephone")
    args = p.parse_args()
    # Les prix max, ranges sous les noms utilises dans FORMATS
    prix = {"prix_dvd": args.prix_dvd, "prix_bluray": args.prix_bluray}

    # --- 2. Mode test : une fausse alerte, puis on s'arrete ---
    if args.test_alerte:
        envoyer_alerte([{"format": "DVD", "prix": 1.0, "titre": "TEST - ceci est un essai", "film": "test",
                         "url": "https://www.vinted.fr/"}], args.ntfy)
        return

    # --- 3. Preparation ---
    log("Surveillance lancee : DVD a {:g} € max, Blu-ray a {:g} € max, toutes les ~{} s. Ctrl+C pour arreter.".format(
        args.prix_dvd, args.prix_bluray, args.intervalle))
    vus = lire_vus()                       # annonces deja vues lors des lancements precedents
    intervalle = args.intervalle           # peut augmenter si Vinted nous bloque
    derniere_ligne = time.time()           # pour afficher un signe de vie toutes les 5 min

    # --- 4. La boucle principale : elle tourne jusqu'a Ctrl+C ---
    while True:
        debut_tour = time.time()
        films = lire_films()   # relu a chaque tour : modifiable sans relancer
        a_alerter, bloque, cles_tour = [], False, []
        # On traite chaque format l'un apres l'autre : DVD, puis Blu-ray (+ 4K)
        for nom_format, categories, cle_prix in FORMATS:
            prix_max = prix[cle_prix]
            # Les annonces vues sont rangees sous une "cle" par format et prix (ex. "DVD|4").
            # Si tu changes un prix max, la cle change et ce format repart a zero.
            cle = "{}|{:g}".format(nom_format, prix_max)
            cles_tour.append(cle)
            premiere = cle not in vus      # True au tout premier passage avec ce prix
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
                break                      # inutile d'insister sur l'autre format
            if premiere:
                vus[cle] = []
            deja_vus = set(vus[cle])
            # au premier passage on examine tout ce qui est en ligne (une relance ne fait rien rater)
            # ensuite, seulement les annonces jamais vues
            a_examiner = annonces if premiere else [a for a in annonces if a["id"] not in deja_vus]
            # On note ces annonces comme "vues" (les 1000 dernieres, pour que le fichier reste petit)
            vus[cle] = (vus[cle] + [a["id"] for a in a_examiner])[-1000:]
            trouves = 0
            for a in a_examiner:
                film = film_trouve(films, a, prix_max)
                if not film:
                    continue                    # pas un film de ta liste
                # Derniere verification : la description
                raison = raison_description(a)
                if raison:
                    log("{:<8} ecartee, {} : {} {}".format(nom_format, raison, format_prix(a["prix"]), a["titre"]), "GRIS")
                else:
                    a.update(format=nom_format, film=film["nom"])   # on note le format et le film trouve
                    a_alerter.append(a)                             # bonne annonce !
                    trouves += 1
                time.sleep(random.uniform(2, 4))   # petite pause entre deux pages d'annonce
            # Message de suivi dans le journal
            if premiere:
                log("{} : {} annonces a {:g} € max deja en ligne, dont {} films d'horreur de ta liste".format(
                    nom_format, len(annonces), prix_max, trouves), "CYAN")
            elif a_examiner or time.time() - derniere_ligne >= 300:
                log("{} : {} nouvelle(s) annonce(s), {} de ta liste".format(nom_format, len(a_examiner), trouves), "GRIS")
            time.sleep(random.uniform(2, 4))       # pause entre la requete DVD et la requete Blu-ray
        if not bloque:
            vus = {k: vus[k] for k in cles_tour if k in vus}   # on oublie les anciens prix
            ecrire_vus(vus)
            if time.time() - derniere_ligne >= 300:
                derniere_ligne = time.time()
        # Envoi des bonnes annonces sur le telephone
        if a_alerter:
            envoyer_alerte(a_alerter, args.ntfy)
        if args.une_fois:
            break                                  # option --une-fois : on s'arrete apres un tour
        # --- Attente avant le tour suivant ---
        if bloque:
            # securite : apres un blocage, on attend 5 min puis on ralentit (jusqu'a 3 min)
            intervalle = min(180, int(intervalle * 1.5))
            log("Pause de 5 min, puis je ralentis : verification toutes les ~{} s".format(intervalle), "JAUNE")
            time.sleep(300)
        else:
            # On attend l'intervalle (+/- 15 % au hasard), moins le temps deja passe pendant ce tour
            reste = intervalle * random.uniform(0.85, 1.15) - (time.time() - debut_tour)
            if reste > 0:
                time.sleep(reste)


# Point d'entree : ce bloc s'execute quand on lance "python3 alerte_dvd.py"
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:              # Ctrl+C : on s'arrete proprement
        log("Surveillance arretee.")
