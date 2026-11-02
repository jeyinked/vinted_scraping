#!/usr/bin/env python3
"""Alerte stock Switch 2 (edition Zelda), version serveur Linux (Ubuntu 22.04 / Python 3.10, sans dependance).

Surveille les pages de produits.txt et envoie une notification ntfy sur le telephone des que
l'une devient disponible. Pas de fenetre ni de navigateur : tout passe par ntfy et par le journal.
Voir LISEZMOI.txt pour l'installation et le lancement en arriere-plan.
"""
# =====================================================================================
#  EN RESUME, CE QUE FAIT CE PROGRAMME, EN BOUCLE, TOUTES LES ~60 SECONDES :
#    1. il relit la liste des pages a surveiller (produits.txt : "Nom | adresse") ;
#    2. il telecharge chaque page, l'une apres l'autre ;
#    3. il en deduit un etat : DISPO, RUPTURE, BLOQUE (le site refuse) ou INCONNU ;
#       (pour editioncollector.fr, il lit plutot la derniere "MISE A JOUR" du site) ;
#    4. si une page PASSE a DISPO, il envoie une notification ntfy sur ton telephone ;
#    5. il memorise les etats (etats.json) pour ne pas re-alerter apres une relance ;
#    6. il attend, puis recommence.
# =====================================================================================

# --- Les "boites a outils" de Python dont on a besoin (toutes fournies avec Python) ---
import argparse            # lire les options de la ligne de commande (--intervalle, --ntfy...)
import html                # transformer "&amp;" en "&" etc. dans les pages web
import json                # lire et ecrire le fichier etats.json
import os                  # manipuler les chemins et dates de fichiers
import random              # tirer des durees d'attente au hasard (pour ne pas etre trop regulier)
import re                  # "expressions regulieres" : chercher des motifs dans du texte
import sys                 # savoir si on ecrit dans un vrai terminal, etc.
import time                # attendre (sleep) et mesurer le temps
import urllib.error        # gerer les erreurs de telechargement
import urllib.request      # telecharger une page web / envoyer une notification
from datetime import datetime   # afficher la date et l'heure dans le journal

# --- Les fichiers utilises, tous places dans le meme dossier que ce script ---
DOSSIER = os.path.dirname(os.path.abspath(__file__))           # le dossier du script
FICHIER_PRODUITS = os.path.join(DOSSIER, "produits.txt")        # les pages a surveiller
FICHIER_LOG = os.path.join(DOSSIER, "alerte.log")               # le journal (historique)
FICHIER_ETATS = os.path.join(DOSSIER, "etats.json")             # le dernier etat connu de chaque page

# Le "User-Agent" est la carte de visite qu'un navigateur donne aux sites.
# On se presente comme un navigateur Chrome ordinaire, sinon beaucoup de sites refusent de repondre.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Mots qui trahissent une page "anti-robot" au lieu de la vraie fiche produit
MOTS_BLOCAGE = ["captcha", "datadome", "incapsula", "request unsuccessful", "access denied",
                "accès refusé", "robot check", "are you a human"]
# Mots qui veulent dire "pas en stock"
MOTS_RUPTURE = ["rupture de stock", "actuellement indisponible", "produit indisponible", "épuisé",
                "plus disponible", "momentanément indisponible", "bientôt disponible",
                "currently unavailable", "out of stock", "sold out", "m.alerter", "m.avertir",
                "prévenez-moi", "product-unavailable"]
# Mots qui veulent dire "on peut acheter"
MOTS_DISPO = ["ajouter au panier", "add to cart", "add-to-cart", "addtocart", "précommander",
              "acheter maintenant"]

# Codes de couleur du terminal pour chaque etat (32 = vert, 90 = gris, 33 = jaune...)
COULEURS = {"DISPO": "32", "RUPTURE": "90", "BLOQUE": "33", "INCONNU": "36", "EPUISE": "90",
            "ALERTE": "1;32", "ERREUR": "31", "INFO": "37"}


def log(message, genre="INFO"):
    """Ecrit un message, precede de la date et l'heure, a l'ecran ET dans alerte.log."""
    ligne = "{}  {}".format(datetime.now().strftime("%d/%m %H:%M:%S"), message)
    if sys.stdout.isatty():
        # Vrai terminal : on ajoute la couleur. "errors=replace" evite un plantage sur un emoji.
        ligne_c = "\033[{}m{}\033[0m".format(COULEURS.get(genre, "37"), ligne)
        print(ligne_c.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    else:
        # Sortie redirigee (fichier, service...) : pas de couleur
        print(ligne.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    with open(FICHIER_LOG, "a", encoding="utf-8") as f:     # "a" = ajouter a la fin sans effacer
        f.write(ligne + "\n")


def lire_produits():
    """Lit produits.txt et renvoie la liste des (nom, adresse) a surveiller."""
    produits = []
    with open(FICHIER_PRODUITS, encoding="utf-8-sig") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#") or "|" not in ligne:
                continue                               # ligne vide, commentaire ou mal formee
            nom, url = ligne.split("|", 1)             # coupe au PREMIER "|" : avant = nom, apres = adresse
            produits.append((nom.strip(), url.strip()))
    return produits


def lire_page(url):
    """Renvoie (code HTTP, html). Code 0 = pas de reponse."""
    # Code HTTP : 200 = OK, 403 = interdit (bloque), 429 = trop de requetes, 503 = indisponible...
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,                    # on se presente comme un navigateur
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "fr-FR,fr;q=0.9",         # on demande la page en francais
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:          # 30 s maximum d'attente
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""                                           # le site a repondu "non"
    except Exception:
        return 0, ""                                                # pas de reseau, delai depasse...


def statut(code, page, url):
    """DISPO, RUPTURE, BLOQUE (le site refuse le robot) ou INCONNU."""
    # Le site a refuse de repondre, ou n'a rien renvoye -> bloque
    if code in (403, 429, 503) or (code == 0 and not page):
        return "BLOQUE"
    h = page.lower()                       # tout en minuscules pour comparer facilement

    # Amazon (toutes langues) : on lit le bloc d'achat de la fiche ; "ajouter au panier" seul
    # ne suffit pas, il apparait aussi sur les produits suggeres
    if "://www.amazon." in url and len(h) >= 50000:
        if 'id="add-to-cart-button"' in h or 'id="buy-now-button"' in h:
            return "DISPO"                 # le vrai bouton "Ajouter au panier" de la fiche est la
        if 'id="outofstock"' in h or 'id="unqualifiedbuybox"' in h:
            return "RUPTURE"               # bloc "Actuellement indisponible" ou "pas livrable chez toi"
        return "INCONNU"

    # 1) donnees structurees schema.org : le plus fiable
    #    (beaucoup de sites cachent dans la page une ligne du type "availability": "InStock" pour Google)
    dispos = re.findall(r'"availability"\s*:\s*"(?:https?://schema\.org/)?(\w+)"', h)
    if dispos:
        if any(d in ("instock", "preorder", "presale", "limitedavailability", "onlineonly") for d in dispos):
            return "DISPO"
        return "RUPTURE"

    # 2) sinon, mots-cles ; une petite page qui parle de captcha est une page de blocage
    #    (une vraie fiche produit fait plus de 50 000 caracteres)
    if len(h) < 50000 and any(re.search(m, h) for m in MOTS_BLOCAGE):
        return "BLOQUE"
    # Les mots de rupture passent AVANT ceux de disponibilite : certains sites affichent
    # le bouton "ajouter au panier" meme quand c'est epuise
    if any(re.search(m, h) for m in MOTS_RUPTURE):
        return "RUPTURE"
    if any(re.search(m, h) for m in MOTS_DISPO):
        return "DISPO"
    return "INCONNU"                       # la page ne dit rien de clair


def derniere_maj(page):
    """editioncollector.fr : la "MISE A JOUR" la plus recente -> (date, texte, epuise, lien) ou None.
    Quand un reassort est fini, le site barre le texte (<del>) et ajoute "Edit : c'est fini"."""
    # La page est un fil de "MISE A JOUR" datees, la plus recente en premier :
    # on decoupe le morceau entre la 1re et la 2e "MISE A JOUR".
    debut = page.find("MISE À JOUR")
    if debut < 0:
        return None
    fin = page.find("MISE À JOUR", debut + 20)
    if fin < 0:
        fin = min(len(page), debut + 20000)
    bloc = re.sub(r"<style[\s\S]*?</style>", "", page[debut:fin])        # enleve la mise en forme
    texte = html.unescape(re.sub(r"<[^>]+>", " ", bloc))                # enleve les balises HTML
    texte = re.sub(r"\s+", " ", texte)                                   # espaces multiples -> un seul
    # On recupere la date ("29 octobre 2026 à 08h38") et le texte de la mise a jour
    m = re.search(r"MISE À JOUR (\d{1,2} \S+ \d{4} à \d{1,2}h\d{2}) (.*?)(?: Afficher toutes les MAJ|$)", texte)
    if not m:
        return None
    resume = m.group(2).strip()
    # Epuise si le texte est barre (<del>) ou s'il dit "c'est fini", "epuise", "termine", "rupture"
    epuise = "<del>" in bloc or bool(re.search(r"c'est (d[ée]j[àa] )?fini|[ée]puis[ée]|termin[ée]|rupture", resume))
    lien = re.search(r'href="(https?://[^"]+)"', bloc)                  # le lien vers le magasin
    return m.group(1), resume[:150], epuise, (lien.group(1) if lien else None)


def envoyer_alerte(nom, url, topic):
    """Ecrit l'alerte dans le journal et l'envoie sur le telephone via ntfy."""
    log("*** DISPO : {} -> {} ***".format(nom, url), "ALERTE")
    if sys.stdout.isatty():
        print("\a", end="", flush=True)   # bip du terminal, si quelqu'un regarde
    if not topic:
        return                            # pas de canal ntfy : on s'arrete la
    # ntfy : on "poste" un message a l'adresse https://ntfy.sh/<nom-du-canal>.
    # Tous les telephones abonnes a ce nom dans l'appli ntfy recoivent la notification.
    req = urllib.request.Request(
        "https://ntfy.sh/" + topic,
        data="{} : {}".format(nom, url).encode("utf-8"),             # le texte
        headers={"Title": "Switch 2 dispo !", "Priority": "urgent",   # titre, priorite maximale
                 "Tags": "rotating_light", "Click": url},                    # icone, lien ouvert au toucher
        method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except Exception as e:
        log("Echec de l'envoi au telephone : {}".format(e), "ERREUR")


# Les etats sont sauvegardes pour ne pas re-alerter a chaque relance pour une alerte deja recue.
# Au-dela de 30 min d'arret on repart de zero : le stock a pu partir puis revenir entre-temps.
def lire_etats():
    """Charge etats.json s'il a ete ecrit il y a moins de 30 minutes, sinon repart de zero."""
    try:
        if time.time() - os.path.getmtime(FICHIER_ETATS) < 30 * 60:
            with open(FICHIER_ETATS, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return {}


def ecrire_etats(etats):
    """Enregistre le dernier etat connu de chaque page dans etats.json."""
    try:
        with open(FICHIER_ETATS, "w", encoding="utf-8") as f:
            json.dump(etats, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def main():
    # --- 1. Les options de la ligne de commande et leurs valeurs par defaut ---
    p = argparse.ArgumentParser(description="Alerte stock Switch 2 (notifications ntfy).")
    p.add_argument("--intervalle", type=int, default=60, help="secondes entre deux tours (defaut 60)")
    p.add_argument("--ntfy", default="switch2-zelda-1jw6pdst", help="canal ntfy du telephone ('' pour desactiver)")
    p.add_argument("--une-fois", action="store_true", help="un seul passage puis arret (pour tester)")
    p.add_argument("--test-alerte", action="store_true", help="envoie une fausse alerte pour tester le telephone")
    args = p.parse_args()

    # --- 2. Mode test : une fausse alerte, puis on s'arrete ---
    if args.test_alerte:
        envoyer_alerte("TEST - ceci est un essai", "https://www.nintendo.com/fr-fr/", args.ntfy)
        return

    # --- 3. Preparation ---
    log("Surveillance lancee (toutes les ~{} s). Ctrl+C pour arreter.".format(args.intervalle))
    etats = lire_etats()                  # dernier etat connu de chaque page (si relance recente)
    if etats:
        log("Reprise de la session precedente : pas de nouvelle alerte pour ce qui a deja ete signale.")
    premier_tour = True                   # au premier tour, on affiche l'etat de toutes les pages

    # --- 4. La boucle principale : elle tourne jusqu'a Ctrl+C ---
    while True:
        # relu a chaque tour : on peut modifier produits.txt sans relancer
        for nom, url in lire_produits():
            code, page = lire_page(url)
            if "editioncollector.fr" in url:
                # --- Cas particulier : le site de suivi des reassorts ---
                maj = derniere_maj(page)
                # L'etat retenu = statut + date de la mise a jour : une NOUVELLE mise a jour
                # non epuisee re-alerte, meme si la precedente etait deja disponible
                if maj is None:
                    cle = "ERREUR"
                elif maj[2]:                       # maj[2] = epuise ?
                    cle = "EPUISE|" + maj[0]       # maj[0] = date
                else:
                    cle = "DISPO|" + maj[0]
                change = cle != etats.get(url)     # different de la derniere fois ?
                if change or premier_tour:
                    if maj is None:
                        log("{:<20} aucune MISE A JOUR trouvee (page bloquee ou modifiee)".format(nom), "BLOQUE")
                    elif maj[2]:
                        log("{:<20} EPUISE   (MAJ du {} : {})".format(nom, maj[0], maj[1]), "EPUISE")
                    elif change:
                        # Nouveau reassort annonce : alerte, avec le lien du magasin (maj[3]) s'il existe
                        envoyer_alerte("{} : {}".format(nom, maj[1]), maj[3] or url, args.ntfy)
                    else:
                        log("{:<20} DISPO    (deja signale, MAJ du {} : {})".format(nom, maj[0], maj[1]), "DISPO")
                etats[url] = cle
            else:
                # --- Cas general : une fiche produit de magasin ---
                s = statut(code, page, url)
                if s == "DISPO" and etats.get(url) != "DISPO":
                    envoyer_alerte(nom, url, args.ntfy)      # on vient de PASSER a dispo : alerte !
                elif s != etats.get(url) or premier_tour:
                    log("{:<20} {}".format(nom, s), s)        # on n'affiche que les changements
                etats[url] = s
            ecrire_etats(etats)
            time.sleep(random.uniform(2, 6))     # petite pause entre deux pages (2 a 6 s au hasard)
        premier_tour = False
        if args.une_fois:
            break                                # option --une-fois : on s'arrete apres un tour
        # Attente avant le tour suivant : l'intervalle + 0 a 30 s au hasard
        time.sleep(args.intervalle + random.uniform(0, 30))


# Point d'entree : ce bloc s'execute quand on lance "python3 alerte_switch2.py"
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:              # Ctrl+C : on s'arrete proprement
        log("Surveillance arretee.")
