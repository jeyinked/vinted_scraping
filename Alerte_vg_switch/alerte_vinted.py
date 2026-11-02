#!/usr/bin/env python3
"""Alerte Vinted, version serveur Linux (Ubuntu 22.04 / Python 3.10, sans dependance).

Lit les nouvelles annonces de jeux Switch sur Vinted (une seule requete pour tous les jeux)
et envoie une notification ntfy des qu'une annonce correspond a une ligne de recherches.txt.
Voir LISEZMOI.txt pour l'installation et le lancement.
"""
# =====================================================================================
#  EN RESUME, CE QUE FAIT CE PROGRAMME, EN BOUCLE, TOUTES LES ~20 SECONDES :
#    1. il relit ta liste de jeux (recherches.txt) ;
#    2. il telecharge la page Vinted "jeux Switch les plus recents, sous 12 €" ;
#    3. il en extrait chaque annonce : numero, titre, prix, lien ;
#    4. il ne garde que les annonces qu'il n'a encore jamais vues (memorisees dans vus.json) ;
#    5. pour chacune, il regarde si elle correspond a un de tes jeux (titre, prix, mots exclus) ;
#    6. si oui, il lit la description pour ecarter les boites vides, jeux sans boite, codes ;
#    7. il envoie les annonces retenues sur ton telephone via ntfy ;
#    8. il attend, puis recommence.
# =====================================================================================

# --- Les "boites a outils" de Python dont on a besoin (toutes fournies avec Python) ---
import argparse            # lire les options de la ligne de commande (--intervalle, --ntfy...)
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
DOSSIER = os.path.dirname(os.path.abspath(__file__))              # le dossier du script
FICHIER_RECHERCHES = os.path.join(DOSSIER, "recherches.txt")       # ta liste de jeux
FICHIER_LOG = os.path.join(DOSSIER, "alerte.log")                  # le journal (historique)
FICHIER_VUS = os.path.join(DOSSIER, "vus.json")                    # les annonces deja vues

# Le "User-Agent" est la carte de visite qu'un navigateur donne aux sites.
# On se presente comme un navigateur Chrome ordinaire, sinon Vinted refuse de repondre.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Codes de couleur du terminal (37 = blanc, 90 = gris, 32 = vert...) pour un journal lisible
COULEURS = {"INFO": "37", "GRIS": "90", "CYAN": "36", "JAUNE": "33", "VERT": "1;32", "ROUGE": "31"}

# Le titre ne dit pas tout : pour les annonces qui ont passe tous les filtres (c'est rare),
# on lit la description pour ecarter les boites sans jeu et, sauf --accepter-sans-boite, les jeux sans boite.
# Si une de ces expressions apparait dans la description -> ce n'est pas un vrai jeu complet, on ecarte.
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
# Expressions qui veulent dire "jeu vendu sans sa boite" (ecartees sauf option --accepter-sans-boite)
PHRASES_SANS_BOITE = [
    "sans boite", "sans boitier", "sans la boite", "sans le boitier", "cartouche seule", "cartouche uniquement",
    "juste la cartouche", "seulement la cartouche", "que la cartouche", "cartouche nue", "jeu seul", "loose",
    "cartridge only", "no case", "no box", "solo cartucho", "sin caja", "solo cartuccia", "senza custodia",
    "senza scatola", "nur modul", "ohne hulle", "ohne ovp",
    "sem caixa", "apenas cartucho", "apenas o cartucho", "so o cartucho", "zonder doos", "alleen cartridge",
    "losse cartridge"]

# Mots trop vagues pour identifier un jeu : on les ignore dans la colonne "mots-cles"
GENERIQUES = {"switch", "nintendo", "jeu", "jeux", "game", "games", "video", "the", "les", "des"}


def log(message, couleur="INFO"):
    """Ecrit un message, precede de la date et l'heure, a l'ecran ET dans alerte.log."""
    ligne = "{}  {}".format(datetime.now().strftime("%d/%m %H:%M:%S"), message)
    if sys.stdout.isatty():
        # On ecrit dans un vrai terminal : on ajoute la couleur.
        # Le "encode/decode ... errors=replace" evite un plantage si le titre contient un emoji
        # que le terminal ne sait pas afficher (il est remplace par "?").
        ligne_c = "\033[{}m{}\033[0m".format(COULEURS.get(couleur, "37"), ligne)
        print(ligne_c.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    else:
        # Sortie redirigee (fichier, service...) : pas de couleur
        print(ligne.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)
    # Ajout a la fin du journal ("a" = append = ajouter sans effacer)
    with open(FICHIER_LOG, "a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def simple(texte):
    """Minuscules sans accents, pour comparer les titres ("Boîte" = "boite")."""
    # NFD separe chaque lettre de son accent ("é" devient "e" + "´"),
    # puis on garde tout sauf les accents (categorie "Mn").
    d = unicodedata.normalize("NFD", str(texte).lower())
    return "".join(c for c in d if unicodedata.category(c) != "Mn")


def mot_present(texte, mot, au_debut=False):
    """Mot ou expression entiere : "vide" trouve "boite vide" mais pas "jeu video"."""
    # On cherche le mot, avec la condition qu'il ne soit ni precede ni suivi d'une lettre ou d'un chiffre.
    # (?<![a-z0-9]) = "pas de lettre juste avant" ; (?![a-z0-9]) = "pas de lettre juste apres".
    # Avec au_debut=True (mots en "^"), le mot doit etre au debut du titre (^\W* = debut + ponctuation eventuelle).
    debut = r"^\W*" if au_debut else r"(?<![a-z0-9])"
    return re.search(debut + re.escape(mot) + r"(?![a-z0-9])", texte) is not None


def lire_recherches():
    """Lit recherches.txt et renvoie la liste de tes jeux, une "fiche" par ligne."""
    recherches = []
    with open(FICHIER_RECHERCHES, encoding="utf-8-sig") as f:
        for ligne in f:
            ligne = ligne.strip()                       # enleve les espaces et le retour a la ligne
            if not ligne or ligne.startswith("#"):
                continue                                # ligne vide ou commentaire : on passe
            # On coupe la ligne a chaque "|". C'est la POSITION qui donne le sens de chaque morceau :
            #   m[0] = nom, m[1] = mots-cles, m[2] = prix max, m[3] = mots exclus, m[4] = titres acceptes
            m = [x.strip() for x in ligne.split("|")]
            if len(m) < 2 or not m[1]:
                continue                                # ligne incomplete : on l'ignore
            try:
                prix = float(m[2].replace(",", ".")) if len(m) >= 3 and m[2] else 0.0   # "12" -> 12.0 ; 0 = pas de limite
            except ValueError:
                prix = 0.0
            # Mots exclus : on decoupe a chaque virgule et on enleve accents/majuscules
            exclus = [simple(x.strip()) for x in m[3].split(",") if x.strip()] if len(m) >= 4 else []
            # le titre devra contenir tous les mots importants ("switch" ne compte pas, "baldur's" -> "baldur")
            mots = [x for x in re.split(r"[^a-z0-9]+", re.sub(r"['’]s\b", "", simple(m[1]))) if x and x not in GENERIQUES]
            cles = [x for x in mots if len(x) >= 3]     # on ignore les mots de 1 ou 2 lettres ("3", "of")
            # 5e colonne facultative : titres acceptes (un seul suffit ; "a+b" exige les deux)
            titres = [simple(x.strip()) for x in m[4].split(",") if x.strip()] if len(m) >= 5 else []
            # La "fiche" du jeu, utilisee plus bas par correspond()
            recherches.append({"nom": m[0], "prix_max": prix, "exclus": exclus, "cles": cles, "titres": titres})
    return recherches


def lire_page(url):
    """Telecharge une page web. Renvoie (code HTTP, contenu). Code 0 = pas de reponse."""
    # Code HTTP : 200 = OK, 403 = interdit (bloque), 404 = introuvable, 429 = trop de requetes...
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


def lire_flux(prix_max, page, plateforme):
    """Une seule requete pour tous les jeux : annonces les plus recentes de la categorie Jeux
    (catalog 3026), plateforme Switch, sous le prix max. Renvoie (bloque, code, annonces)."""
    # On construit l'adresse de recherche Vinted, comme si on reglait les filtres sur le site :
    #   catalog[]=3026                 -> categorie "Jeux video et consoles > Jeux"
    #   order=newest_first             -> les plus recentes d'abord
    #   video_game_platform_ids[]=1273 -> plateforme Nintendo Switch
    #   price_to=12                    -> 12 € maximum
    # (%5B%5D est la facon d'ecrire "[]" dans une adresse web)
    url = "https://www.vinted.fr/catalog?catalog%5B%5D=3026&order=newest_first&page={}".format(page)
    if plateforme:
        url += "&video_game_platform_ids%5B%5D=" + plateforme
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
    #   <a href="/items/10570502386-nom-du-jeu" ... title="Nom du jeu, État: ..., 10.00 €, 11.20 €">
    # On recupere le numero (groupe 1) et le titre complet (groupe 2) de chaque annonce.
    annonces, ids = [], set()
    for m in re.finditer(r'<a href="/items/(\d+)-[^"]*"[^>]*?\btitle="([^"]*)"', page_html):
        if m.group(1) in ids:
            continue   # une meme annonce apparait parfois deux fois dans la page
        ids.add(m.group(1))
        # titre Vinted : "Nom, Etat: ..., 10.00 €, 11.20 €" (prix, puis prix avec protection acheteur)
        complet = html.unescape(m.group(2))
        # Le nom du jeu = tout ce qui est avant ", État:" (ou ", Marque:", ", Taille:")
        nom = re.match(r"^(.*?), (?:État|Marque|Taille)\s*:", complet)
        nom = nom.group(1) if nom else re.split(r", \d", complet)[0]
        # Le prix = le premier "nombre suivi de €" (le 2e est le prix avec protection acheteur)
        p = re.search(r"(\d+(?:[.,]\d+)?)\s*€", complet)
        annonces.append({"id": m.group(1), "titre": nom.strip(),
                         "prix": float(p.group(1).replace(",", ".")) if p else None,
                         "url": "https://www.vinted.fr/items/" + m.group(1)})
    return False, code, annonces


def correspond(rch, a, console):
    """Prix max (hors protection acheteur), titre attendu, aucun mot exclu, console si demandee.
    rch = la fiche d'un de tes jeux ; a = une annonce. Renvoie True si l'annonce t'interesse."""
    # 1) Trop cher -> non
    if rch["prix_max"] > 0 and a["prix"] is not None and a["prix"] > rch["prix_max"]:
        return False
    titre = simple(a["titre"])
    # 2) Le titre parle-t-il bien de ce jeu ?
    if rch["titres"]:
        # Avec une liste de titres acceptes : il en faut au moins UN ("any").
        # "a+b" est coupe en a et b, qui doivent etre presents TOUS LES DEUX ("all").
        ok = any(all(mot_present(titre, p.strip()) for p in t.split("+") if p.strip()) for t in rch["titres"])
    else:
        # Sinon : tous les mots-cles importants doivent etre dans le titre
        ok = all(c in titre for c in rch["cles"])
    if not ok:
        return False
    # 3) Un seul mot exclu dans le titre -> non
    # un mot exclu qui commence par ^ ne compte qu'en debut de titre ("^boitier")
    if any(mot_present(titre, e.lstrip("^"), au_debut=e.startswith("^")) for e in rch["exclus"]):
        return False
    # 4) Option --console : le titre doit en plus contenir ce mot (desactive par defaut)
    return not console or mot_present(titre, console)


def raison_description(a, accepter_sans_boite):
    """Lit la description de l'annonce ; renvoie la raison de l'ecarter, ou None."""
    # Une requete de plus, mais seulement pour les annonces qui ont deja passe tous les filtres (rare)
    code, page = lire_page(a["url"])
    # La description est dans une balise <meta name="description" content="...">
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
    return None       # rien de suspect : l'annonce est bonne


def format_prix(prix):
    """12.5 -> "12,50 €" (format francais)."""
    return "? €" if prix is None else "{:.2f} €".format(prix).replace(".", ",")


def envoyer_alerte(annonces, topic):
    """Ecrit les annonces dans le journal et les envoie sur le telephone via ntfy."""
    for a in annonces:
        log("*** NOUVELLE : {} - {} -> {} ***".format(format_prix(a["prix"]), a["titre"], a["url"]), "VERT")
    if sys.stdout.isatty():
        print("\a", end="", flush=True)   # bip du terminal, si quelqu'un regarde
    if not topic:
        return                            # pas de canal ntfy : on s'arrete la
    # ntfy : on "poste" un message a l'adresse https://ntfy.sh/<nom-du-canal>.
    # Tous les telephones abonnes a ce nom dans l'appli ntfy recoivent la notification.
    for a in annonces[:3]:                # au plus 3 notifications d'un coup
        req = urllib.request.Request(
            "https://ntfy.sh/" + topic,
            data="{} - {}".format(format_prix(a["prix"]), a["titre"]).encode("utf-8"),   # le texte
            headers={"Title": "Vinted : nouvelle annonce",   # le titre de la notification
                     "Priority": "urgent",                    # priorite maximale (son fort)
                     "Tags": "shopping_cart",                 # l'icone (un caddie)
                     "Click": a["url"]},                      # toucher la notif ouvre l'annonce
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=15).close()
        except Exception as e:
            log("Echec de l'envoi au telephone : {}".format(e), "ROUGE")


def lire_vus():
    """Charge vus.json : la liste des numeros d'annonces deja vues (vide au tout premier lancement)."""
    try:
        with open(FICHIER_VUS, encoding="utf-8-sig") as f:
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
    p = argparse.ArgumentParser(description="Alerte Vinted : nouvelles annonces de jeux Switch (notifications ntfy).")
    p.add_argument("--intervalle", type=int, default=20, help="secondes entre deux verifications (defaut 20)")
    p.add_argument("--plateforme", default="1273", help="filtre Plateforme de Vinted, 1273 = Switch ('' pour toutes)")
    p.add_argument("--console", default="", help="mot obligatoire en plus dans le titre (ex. switch)")
    p.add_argument("--ntfy", default="switch2-zelda-1jw6pdst", help="canal ntfy du telephone ('' pour desactiver)")
    p.add_argument("--accepter-sans-boite", action="store_true", help="accepte aussi les jeux vendus sans boite")
    p.add_argument("--une-fois", action="store_true", help="un seul passage puis arret (pour tester)")
    p.add_argument("--test-alerte", action="store_true", help="envoie une fausse alerte pour tester le telephone")
    args = p.parse_args()

    # --- 2. Mode test : une fausse alerte, puis on s'arrete ---
    if args.test_alerte:
        envoyer_alerte([{"titre": "TEST - ceci est un essai", "prix": 5.0, "url": "https://www.vinted.fr/"}], args.ntfy)
        return

    # --- 3. Preparation ---
    log("Surveillance Vinted lancee : tous tes jeux verifies ensemble toutes les ~{} s. Ctrl+C pour arreter.".format(args.intervalle))
    vus = lire_vus()                       # annonces deja vues lors des lancements precedents
    intervalle = args.intervalle           # peut augmenter si Vinted nous bloque
    derniere_ligne = time.time()           # pour afficher un signe de vie toutes les 5 min

    # --- 4. La boucle principale : elle tourne jusqu'a Ctrl+C ---
    while True:
        debut_tour = time.time()
        recherches = lire_recherches()   # relu a chaque tour : modifiable sans relancer
        if not recherches:
            time.sleep(intervalle)       # liste vide : on attend et on reessaie
            continue
        # On demande a Vinted les annonces jusqu'au plus haut prix max de ta liste ;
        # le prix max de chaque jeu est verifie ensuite dans correspond().
        prix_max = max(r["prix_max"] for r in recherches)
        # Les annonces vues sont rangees sous une "cle" (ex. "flux|12|p1273").
        # Si tu changes de prix max ou de plateforme, la cle change et on repart a zero.
        cle = "flux|{:g}|p{}".format(prix_max, args.plateforme)
        premiere = cle not in vus          # True au tout premier passage avec ces reglages

        # page 1 = les annonces les plus recentes ; si elles sont toutes nouvelles, on lit aussi la page 2
        # (ca arrive si plus de 96 annonces ont ete publiees depuis la derniere verification)
        bloque, code, annonces = lire_flux(prix_max, 1, args.plateforme)
        if not bloque and not premiere and annonces and not any(a["id"] in vus[cle] for a in annonces):
            time.sleep(2)
            b2, _, page2 = lire_flux(prix_max, 2, args.plateforme)
            if not b2:
                deja = {a["id"] for a in annonces}
                annonces += [a for a in page2 if a["id"] not in deja]

        a_alerter = []                     # les annonces a envoyer sur le telephone ce tour-ci
        if bloque:
            log("BLOQUE par Vinted (code {})".format(code), "JAUNE")
        else:
            if premiere:
                vus[cle] = []
            # au premier passage on examine tout ce qui est en ligne (une relance ne fait rien rater)
            # ensuite, seulement les annonces jamais vues
            deja_vus = set(vus[cle])
            a_examiner = annonces if premiere else [a for a in annonces if a["id"] not in deja_vus]
            # On note ces annonces comme "vues" (on garde les 1000 dernieres, pour que le fichier reste petit)
            vus[cle].extend(a["id"] for a in a_examiner)
            vus[cle] = vus[cle][-1000:]
            vus = {cle: vus[cle]}   # on oublie les anciens formats ou un autre prix
            for a in a_examiner:
                # On cherche le premier de tes jeux auquel l'annonce correspond (ou None si aucun)
                rch = next((r for r in recherches if correspond(r, a, args.console)), None)
                if not rch:
                    continue                   # l'annonce ne t'interesse pas
                # Derniere verification : la description
                raison = raison_description(a, args.accepter_sans_boite)
                if raison:
                    log("{:<24} ecartee, {} : {} {}".format(rch["nom"], raison, format_prix(a["prix"]), a["titre"]), "GRIS")
                else:
                    a_alerter.append(a)        # bonne annonce !
                time.sleep(random.uniform(2, 4))   # petite pause entre deux pages d'annonce
            ecrire_vus(vus)
            # Message de suivi dans le journal
            if premiere:
                log("{} annonces Switch a {:g} € max deja en ligne, dont {} correspondant a tes criteres".format(
                    len(annonces), prix_max, len(a_alerter)), "CYAN")
            elif a_examiner or time.time() - derniere_ligne >= 300:
                # une ligne quand il y a du nouveau, sinon une toutes les 5 min pour montrer que ca tourne
                log("Verification : {} nouvelle(s) annonce(s), {} correspondant a tes criteres".format(
                    len(a_examiner), len(a_alerter)), "GRIS")
                derniere_ligne = time.time()
        # Envoi des bonnes annonces sur le telephone
        if a_alerter:
            envoyer_alerte(a_alerter, args.ntfy)
        if args.une_fois:
            break                              # option --une-fois : on s'arrete apres un tour
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


# Point d'entree : ce bloc s'execute quand on lance "python3 alerte_vinted.py"
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:              # Ctrl+C : on s'arrete proprement
        log("Surveillance arretee.")
