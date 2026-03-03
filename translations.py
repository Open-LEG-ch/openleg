"""Lightweight dict-based i18n for OpenLEG.
No flask-babel overhead. Tenant config language field drives selection.
"""

# Kanton -> language mapping for all 26 Swiss cantons
KANTON_LANGUAGE = {
    'ZH': 'de', 'BE': 'de', 'LU': 'de', 'UR': 'de', 'SZ': 'de',
    'OW': 'de', 'NW': 'de', 'GL': 'de', 'ZG': 'de', 'SO': 'de',
    'BS': 'de', 'BL': 'de', 'SH': 'de', 'AR': 'de', 'AI': 'de',
    'SG': 'de', 'AG': 'de', 'TG': 'de',
    'VD': 'fr', 'GE': 'fr', 'NE': 'fr', 'JU': 'fr',
    'FR': 'fr', 'VS': 'fr',
    'TI': 'it',
    'GR': 'rm',
}

TRANSLATIONS = {
    # === Navigation (6 keys) ===
    'nav_stromgemeinschaft': {
        'de': 'Stromgemeinschaft gründen',
        'fr': 'Fonder une communauté électrique',
        'it': 'Fondare una comunità elettrica',
        'rm': 'Fundar ina cuminanza electrica',
    },
    'nav_fuer_gemeinden': {
        'de': 'Für Gemeinden',
        'fr': 'Pour les communes',
        'it': 'Per i comuni',
        'rm': 'Per las vischnancas',
    },
    'nav_so_funktionierts': {
        'de': "So funktioniert's",
        'fr': 'Comment ça marche',
        'it': 'Come funziona',
        'rm': 'Co funcziunai',
    },
    'nav_open_source': {
        'de': 'Open Source',
        'fr': 'Open Source',
        'it': 'Open Source',
        'rm': 'Open Source',
    },
    'nav_gemeinde_cta': {
        'de': 'Wie Ihre Gemeinde teilnimmt',
        'fr': 'Comment votre commune participe',
        'it': 'Come partecipa il vostro comune',
        'rm': 'Co partecipar vossa vischnanca',
    },
    'brand_tagline': {
        'de': 'Freie Infrastruktur für Schweizer Stromgemeinschaften',
        'fr': 'Infrastructure libre pour les communautés électriques suisses',
        'it': 'Infrastruttura libera per le comunità elettriche svizzere',
        'rm': 'Infrastructura libra per cuminanzas electricas svizras',
    },

    # === Hero/landing (8 keys) ===
    'hero_title': {
        'de': 'Ihr Strom. Ihre Nachbarn. Ihre Gemeinschaft.',
        'fr': 'Votre courant. Vos voisins. Votre communauté.',
        'it': 'La vostra energia. I vostri vicini. La vostra comunità.',
        'rm': 'Voss current. Voss vischins. Vossa cuminanza.',
    },
    'hero_subtitle': {
        'de': 'Gründen Sie eine lokale Stromgemeinschaft (LEG) und sparen Sie bis zu CHF 270/Jahr auf Netzgebühren. Kostenlos seit 2026, Open Source, Ihre Daten bleiben bei Ihnen.',
        'fr': 'Fondez une communauté électrique locale (CEL) et économisez jusqu\'à CHF 270/an sur les frais de réseau. Gratuit depuis 2026, Open Source, vos données restent chez vous.',
        'it': 'Fondate una comunità elettrica locale (CEL) e risparmiate fino a CHF 270/anno sulle tariffe di rete. Gratuito dal 2026, Open Source, i vostri dati restano da voi.',
        'rm': 'Fundai ina cuminanza electrica locala (CEL) e spargni fin a CHF 270/onn sin las taxas da rait. Gratuit dapi 2026, Open Source.',
    },
    'hero_gemeinde_title': {
        'de': 'Ihre Gemeinde ermöglicht die Energiewende.',
        'fr': 'Votre commune rend la transition énergétique possible.',
        'it': 'Il vostro comune rende possibile la transizione energetica.',
        'rm': 'Vossa vischnanca renda pusseivel la transaziun energetica.',
    },
    'hero_gemeinde_subtitle': {
        'de': 'Eigene Seite für Ihre Gemeinde, Bewohner finden Nachbarn, Stromgemeinschaften entstehen. Sie füllen ein Formular aus, wir übernehmen Technik und Betrieb.',
        'fr': 'Une page dédiée à votre commune, les habitants trouvent leurs voisins, les communautés se forment. Vous remplissez un formulaire, nous gérons la technique.',
        'it': 'Una pagina dedicata al vostro comune, i residenti trovano i vicini, le comunità si formano. Compilate un formulario, noi gestiamo la tecnica.',
    },
    'cta_adresse_pruefen': {
        'de': 'Adresse prüfen',
        'fr': 'Vérifier l\'adresse',
        'it': 'Verificare l\'indirizzo',
        'rm': 'Verifitgar l\'adressa',
    },
    'cta_mehr_bewohner': {
        'de': 'Mehr für Bewohner',
        'fr': 'Plus pour les habitants',
        'it': 'Più per i residenti',
    },
    'cta_gemeinde_anmelden': {
        'de': 'Gemeinde anmelden',
        'fr': 'Inscrire la commune',
        'it': 'Iscrivere il comune',
        'rm': 'Annunziar la vischnanca',
    },
    'cta_so_funktionierts': {
        'de': "So funktioniert's",
        'fr': 'Comment ça marche',
        'it': 'Come funziona',
    },

    # === Trust bar badges (4 keys) ===
    'badge_kostenlos': {
        'de': 'Kostenlos, für immer',
        'fr': 'Gratuit, pour toujours',
        'it': 'Gratuito, per sempre',
        'rm': 'Gratuit, per adina',
    },
    'badge_open_source': {
        'de': 'Open Source',
        'fr': 'Open Source',
        'it': 'Open Source',
        'rm': 'Open Source',
    },
    'badge_daten_bleiben': {
        'de': 'Ihre Daten bleiben bei Ihnen',
        'fr': 'Vos données restent chez vous',
        'it': 'I vostri dati restano da voi',
        'rm': 'Voss datas restan tar vus',
    },
    'badge_schweiz': {
        'de': 'Gehostet in der Schweiz',
        'fr': 'Hébergé en Suisse',
        'it': 'Ospitato in Svizzera',
        'rm': 'Hosted en Svizra',
    },

    # === Trust bar data sources ===
    'trust_datenquellen': {
        'de': 'Datenquellen:',
        'fr': 'Sources de données:',
        'it': 'Fonti di dati:',
        'rm': 'Funtaunas da datas:',
    },
    'trust_kein_datenverkauf': {
        'de': 'Kein Datenverkauf',
        'fr': 'Aucune vente de données',
        'it': 'Nessuna vendita di dati',
        'rm': 'Nagina vendita da datas',
    },

    # === Bewohner flow (12 keys) ===
    'bewohner_title': {
        'de': 'OpenLEG für Bewohner',
        'fr': 'OpenLEG pour les habitants',
        'it': 'OpenLEG per i residenti',
        'rm': 'OpenLEG per abitants',
    },
    'bewohner_subtitle': {
        'de': 'OpenLEG koordiniert den gesamten Prozess: Sie prüfen Ihre Adresse, finden Nachbarn mit Solarstrom und treten einer lokalen Stromgemeinschaft bei.',
        'fr': 'OpenLEG coordonne tout le processus: vérifiez votre adresse, trouvez des voisins avec du solaire et rejoignez une communauté électrique locale.',
        'it': 'OpenLEG coordina l\'intero processo: verificate il vostro indirizzo, trovate vicini con solare e aderite a una comunità elettrica locale.',
    },
    'bewohner_eigentuemer_title': {
        'de': 'Eigentümer mit PV',
        'fr': 'Propriétaires avec PV',
        'it': 'Proprietari con FV',
    },
    'bewohner_mieter_title': {
        'de': 'Mieter und Haushalte ohne eigene PV',
        'fr': 'Locataires et ménages sans PV',
        'it': 'Inquilini e famiglie senza FV',
    },
    'bewohner_ehrlich_title': {
        'de': 'Ehrlich zu Einsparungen',
        'fr': 'Honnête sur les économies',
        'it': 'Onesti sui risparmi',
    },
    'bewohner_koordination_title': {
        'de': 'Das eigentliche Problem: Koordination',
        'fr': 'Le vrai problème: la coordination',
        'it': 'Il vero problema: il coordinamento',
    },
    'nav_fuer_bewohner': {
        'de': 'Für Bewohner',
        'fr': 'Pour les habitants',
        'it': 'Per i residenti',
        'rm': 'Per abitants',
    },
    'bewohner_adresse_label': {
        'de': 'Ihre Adresse',
        'fr': 'Votre adresse',
        'it': 'Il vostro indirizzo',
    },
    'bewohner_email_label': {
        'de': 'E-Mail',
        'fr': 'E-mail',
        'it': 'E-mail',
    },
    'bewohner_consent_text': {
        'de': 'Ich stimme der Datenschutzerklärung zu',
        'fr': 'J\'accepte la politique de confidentialité',
        'it': 'Accetto la politica sulla privacy',
    },
    'bewohner_success': {
        'de': 'Registrierung erfolgreich! Wir melden uns.',
        'fr': 'Inscription réussie! Nous vous contacterons.',
        'it': 'Registrazione riuscita! Vi contatteremo.',
    },

    # === Gemeinden flow (10 keys) ===
    'gemeinden_title': {
        'de': 'OpenLEG für Gemeinden',
        'fr': 'OpenLEG pour les communes',
        'it': 'OpenLEG per i comuni',
    },
    'gemeinden_subtitle': {
        'de': 'Zwei Wege, ein Ziel: funktionierende Lokale Elektrizitätsgemeinschaften mit klarer Zuständigkeit zwischen Gemeinde, Bewohnern und Technikbetrieb.',
        'fr': 'Deux voies, un objectif: des communautés électriques locales fonctionnelles avec une répartition claire des responsabilités.',
        'it': 'Due vie, un obiettivo: comunità elettriche locali funzionanti con una chiara ripartizione delle responsabilità.',
    },
    'gemeinden_selbst_title': {
        'de': 'Selbst betreiben',
        'fr': 'Exploiter soi-même',
        'it': 'Gestione autonoma',
    },
    'gemeinden_managed_title': {
        'de': 'Gehostet mit optionalem Projektsupport',
        'fr': 'Hébergé avec support projet optionnel',
        'it': 'Ospitato con supporto progetto opzionale',
    },
    'gemeinden_entlastung_title': {
        'de': 'Was Gemeinden im Alltag entlastet',
        'fr': 'Ce qui soulage les communes au quotidien',
        'it': 'Cosa alleggerisce i comuni nel quotidiano',
    },
    'gemeinden_abgrenzung_title': {
        'de': 'Klare Abgrenzung',
        'fr': 'Délimitation claire',
        'it': 'Delimitazione chiara',
    },
    'gemeinden_kommunikation_title': {
        'de': 'Kommunikation und Beteiligung',
        'fr': 'Communication et participation',
        'it': 'Comunicazione e partecipazione',
    },
    'gemeinden_prozesssicherheit_title': {
        'de': 'Prozesssicherheit',
        'fr': 'Sécurité des processus',
        'it': 'Sicurezza dei processi',
    },
    'gemeinden_anfrage': {
        'de': 'Anfrage stellen',
        'fr': 'Envoyer une demande',
        'it': 'Inviare una richiesta',
    },
    'gemeinden_projektsupport': {
        'de': 'Projektsupport anfragen',
        'fr': 'Demander un support projet',
        'it': 'Richiedere supporto progetto',
    },

    # === How-it-works (8 keys) ===
    'hiw_title': {
        'de': 'So funktioniert eine Stromgemeinschaft',
        'fr': 'Comment fonctionne une communauté électrique',
        'it': 'Come funziona una comunità elettrica',
    },
    'hiw_step1_title': {
        'de': 'Adresse prüfen',
        'fr': 'Vérifier l\'adresse',
        'it': 'Verificare l\'indirizzo',
    },
    'hiw_step2_title': {
        'de': 'Nachbarn finden',
        'fr': 'Trouver des voisins',
        'it': 'Trovare i vicini',
    },
    'hiw_step3_title': {
        'de': 'Stromgemeinschaft gründen',
        'fr': 'Fonder la communauté électrique',
        'it': 'Fondare la comunità elettrica',
    },
    'hiw_step4_title': {
        'de': 'Netzbetreiber melden',
        'fr': 'Informer le gestionnaire de réseau',
        'it': 'Notificare il gestore di rete',
    },
    'hiw_step5_title': {
        'de': 'Strom teilen, Geld sparen',
        'fr': 'Partager l\'électricité, économiser',
        'it': 'Condividere l\'energia, risparmiare',
    },
    'hiw_rechtsgrundlage': {
        'de': 'Rechtsgrundlage',
        'fr': 'Base légale',
        'it': 'Base legale',
    },
    'hiw_subtitle': {
        'de': 'Von der Idee zur aktiven LEG: was Bewohner und Gemeinden wissen müssen.',
        'fr': 'De l\'idée à la CEL active: ce que les habitants et communes doivent savoir.',
        'it': 'Dall\'idea alla CEL attiva: cosa devono sapere residenti e comuni.',
    },

    # === Pricing (6 keys) ===
    'pricing_title': {
        'de': 'Kostenlos. Open Source. Für immer.',
        'fr': 'Gratuit. Open Source. Pour toujours.',
        'it': 'Gratuito. Open Source. Per sempre.',
        'rm': 'Gratuit. Open Source. Per adina.',
    },
    'pricing_subtitle': {
        'de': 'OpenLEG ist freie Infrastruktur für Schweizer Stromgemeinschaften. Keine versteckten Kosten, kein Vendor Lock-in, keine Daten die verkauft werden.',
        'fr': 'OpenLEG est une infrastructure libre pour les communautés électriques suisses. Pas de coûts cachés, pas de vendor lock-in, pas de vente de données.',
        'it': 'OpenLEG è un\'infrastruttura libera per le comunità elettriche svizzere. Nessun costo nascosto, nessun vendor lock-in, nessuna vendita di dati.',
    },
    'pricing_bewohner': {
        'de': 'Für Bewohner',
        'fr': 'Pour les habitants',
        'it': 'Per i residenti',
    },
    'pricing_gemeinden': {
        'de': 'Für Gemeinden',
        'fr': 'Pour les communes',
        'it': 'Per i comuni',
    },
    'pricing_api': {
        'de': 'Freie API',
        'fr': 'API libre',
        'it': 'API libera',
    },
    'pricing_finanzierung_title': {
        'de': 'Wie wird das finanziert?',
        'fr': 'Comment est-ce financé?',
        'it': 'Come viene finanziato?',
    },

    # === Partials (10 keys) ===
    'footer_tagline': {
        'de': 'Freie Infrastruktur für Schweizer Stromgemeinschaften. Open Source, gehostet in der Schweiz.',
        'fr': 'Infrastructure libre pour les communautés électriques suisses. Open Source, hébergé en Suisse.',
        'it': 'Infrastruttura libera per le comunità elettriche svizzere. Open Source, ospitato in Svizzera.',
        'rm': 'Infrastructura libra per cuminanzas electricas svizras. Open Source, hosted en Svizra.',
    },
    'footer_impressum': {
        'de': 'Impressum',
        'fr': 'Mentions légales',
        'it': 'Impressum',
    },
    'footer_datenschutz': {
        'de': 'Datenschutz',
        'fr': 'Protection des données',
        'it': 'Protezione dei dati',
    },
    'footer_open_source_swiss': {
        'de': 'Open Source. Made in Switzerland.',
        'fr': 'Open Source. Made in Switzerland.',
        'it': 'Open Source. Made in Switzerland.',
        'rm': 'Open Source. Made in Switzerland.',
    },
    'savings_title': {
        'de': 'Sparpotenzial Ihrer Gemeinde',
        'fr': 'Potentiel d\'économies de votre commune',
        'it': 'Potenziale di risparmio del vostro comune',
    },
    'savings_placeholder': {
        'de': 'Gemeinde oder BFS-Nr.',
        'fr': 'Commune ou n° OFS',
        'it': 'Comune o n. UST',
    },
    'savings_check': {
        'de': 'Prüfen',
        'fr': 'Vérifier',
        'it': 'Verificare',
    },
    'savings_chf_year': {
        'de': 'CHF/Jahr Einsparpotenzial',
        'fr': 'CHF/an potentiel d\'économies',
        'it': 'CHF/anno potenziale di risparmio',
    },
    'savings_solar': {
        'de': 'Solarpotenzial',
        'fr': 'Potentiel solaire',
        'it': 'Potenziale solare',
    },
    'savings_gap': {
        'de': 'Tariflücke',
        'fr': 'Écart tarifaire',
        'it': 'Divario tariffario',
    },

    # === Error messages (8 keys) ===
    'error_not_found': {
        'de': 'Gemeinde nicht gefunden',
        'fr': 'Commune introuvable',
        'it': 'Comune non trovato',
    },
    'error_no_data': {
        'de': 'Daten nicht verfügbar für diese Gemeinde',
        'fr': 'Données non disponibles pour cette commune',
        'it': 'Dati non disponibili per questo comune',
    },
    'error_network': {
        'de': 'Netzwerkfehler. Bitte erneut versuchen.',
        'fr': 'Erreur réseau. Veuillez réessayer.',
        'it': 'Errore di rete. Riprova.',
    },
    'error_invalid_email': {
        'de': 'Bitte eine gültige E-Mail-Adresse eingeben',
        'fr': 'Veuillez entrer une adresse e-mail valide',
        'it': 'Inserire un indirizzo e-mail valido',
    },
    'error_address_required': {
        'de': 'Bitte eine Adresse eingeben',
        'fr': 'Veuillez entrer une adresse',
        'it': 'Inserire un indirizzo',
    },
    'error_consent_required': {
        'de': 'Bitte der Datenschutzerklärung zustimmen',
        'fr': 'Veuillez accepter la politique de confidentialité',
        'it': 'Accettare la politica sulla privacy',
    },
    'error_server': {
        'de': 'Serverfehler. Bitte später versuchen.',
        'fr': 'Erreur serveur. Réessayez plus tard.',
        'it': 'Errore del server. Riprovare più tardi.',
    },
    'error_generic': {
        'de': 'Ein Fehler ist aufgetreten',
        'fr': 'Une erreur s\'est produite',
        'it': 'Si è verificato un errore',
    },

    # === Email subjects (6 keys) ===
    'email_welcome_subject': {
        'de': 'Willkommen! Ihre Nachbarn warten',
        'fr': 'Bienvenue! Vos voisins vous attendent',
        'it': 'Benvenuti! I vostri vicini vi aspettano',
    },
    'email_smartmeter_subject': {
        'de': 'Schnelle Frage: Haben Sie einen Smart Meter?',
        'fr': 'Question rapide: avez-vous un compteur intelligent?',
        'it': 'Domanda rapida: avete uno smart meter?',
    },
    'email_consumption_subject': {
        'de': 'Optimieren Sie Ihr LEG-Matching',
        'fr': 'Optimisez votre matching CEL',
        'it': 'Ottimizzate il vostro matching CEL',
    },
    'email_formation_subject': {
        'de': 'Ihre LEG-Gemeinschaft kann starten',
        'fr': 'Votre communauté CEL peut démarrer',
        'it': 'La vostra comunità CEL può partire',
    },
    'email_nudge_subject': {
        'de': 'Ihre LEG-Gründung wartet',
        'fr': 'Votre fondation CEL attend',
        'it': 'La vostra fondazione CEL attende',
    },
    'email_outreach_subject': {
        'de': 'Freie Infrastruktur für Ihre Gemeinde',
        'fr': 'Infrastructure libre pour votre commune',
        'it': 'Infrastruttura libera per il vostro comune',
    },

    # === Misc (6 keys) ===
    'data_policy': {
        'de': 'Ihre Daten bleiben im LEG-Kontext. Kein Datenverkauf an Dritte.',
        'fr': 'Vos données restent dans le contexte CEL. Aucune vente à des tiers.',
        'it': 'I vostri dati restano nel contesto CEL. Nessuna vendita a terzi.',
        'rm': 'Voss datas restan en il context CEL. Nagina vendita a terzas partidas.',
    },
    'legal_agpl': {
        'de': 'Lizenziert unter AGPL-3.0',
        'fr': 'Sous licence AGPL-3.0',
        'it': 'Licenza AGPL-3.0',
    },
    'onboarding_title': {
        'de': 'Stromgemeinschaft starten',
        'fr': 'Démarrer une communauté électrique',
        'it': 'Avviare una comunità elettrica',
    },
    'onboarding_subtitle': {
        'de': 'OpenLEG ist freie, Open Source Infrastruktur für Schweizer Lokale Elektrizitätsgemeinschaften. Wählen Sie Ihren Einstieg.',
        'fr': 'OpenLEG est une infrastructure libre et Open Source pour les communautés électriques locales suisses. Choisissez votre accès.',
        'it': 'OpenLEG è un\'infrastruttura libera e Open Source per le comunità elettriche locali svizzere. Scegliete il vostro accesso.',
    },
    'leg_full_name': {
        'de': 'Lokale Elektrizitätsgemeinschaft',
        'fr': 'Communauté électrique locale',
        'it': 'Comunità elettrica locale',
        'rm': 'Cuminanza electrica locala',
    },
    'leg_short': {
        'de': 'LEG',
        'fr': 'CEL',
        'it': 'CEL',
        'rm': 'CEL',
    },
}


def t(key, lang='de'):
    """Look up translation. Falls back: requested lang -> de -> key itself."""
    entry = TRANSLATIONS.get(key, {})
    return entry.get(lang) or entry.get('de') or key
