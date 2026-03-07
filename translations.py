"""Lightweight dict-based i18n for OpenLEG.
No flask-babel overhead. Tenant config language field drives selection.
"""

# Kanton -> language mapping for all 26 Swiss cantons
KANTON_LANGUAGE = {
    'ZH': 'de',
    'BE': 'de',
    'LU': 'de',
    'UR': 'de',
    'SZ': 'de',
    'OW': 'de',
    'NW': 'de',
    'GL': 'de',
    'ZG': 'de',
    'SO': 'de',
    'BS': 'de',
    'BL': 'de',
    'SH': 'de',
    'AR': 'de',
    'AI': 'de',
    'SG': 'de',
    'AG': 'de',
    'TG': 'de',
    'VD': 'fr',
    'GE': 'fr',
    'NE': 'fr',
    'JU': 'fr',
    'FR': 'fr',
    'VS': 'fr',
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
        'fr': "Fondez une communauté électrique locale (CEL) et économisez jusqu'à CHF 270/an sur les frais de réseau. Gratuit depuis 2026, Open Source, vos données restent chez vous.",
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
        'fr': "Vérifier l'adresse",
        'it': "Verificare l'indirizzo",
        'rm': "Verifitgar l'adressa",
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
        'it': "OpenLEG coordina l'intero processo: verificate il vostro indirizzo, trovate vicini con solare e aderite a una comunità elettrica locale.",
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
        'fr': "J'accepte la politique de confidentialité",
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
        'fr': "Vérifier l'adresse",
        'it': "Verificare l'indirizzo",
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
        'fr': "Partager l'électricité, économiser",
        'it': "Condividere l'energia, risparmiare",
    },
    'hiw_rechtsgrundlage': {
        'de': 'Rechtsgrundlage',
        'fr': 'Base légale',
        'it': 'Base legale',
    },
    'hiw_subtitle': {
        'de': 'Von der Idee zur aktiven LEG: was Bewohner und Gemeinden wissen müssen.',
        'fr': "De l'idée à la CEL active: ce que les habitants et communes doivent savoir.",
        'it': "Dall'idea alla CEL attiva: cosa devono sapere residenti e comuni.",
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
        'it': "OpenLEG è un'infrastruttura libera per le comunità elettriche svizzere. Nessun costo nascosto, nessun vendor lock-in, nessuna vendita di dati.",
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
        'fr': "Potentiel d'économies de votre commune",
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
        'fr': "CHF/an potentiel d'économies",
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
        'fr': "Une erreur s'est produite",
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
        'it': "OpenLEG è un'infrastruttura libera e Open Source per le comunità elettriche locali svizzere. Scegliete il vostro accesso.",
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
    # === Fuer Bewohner page (~40 keys) ===
    'bew_page_title': {
        'de': 'Solarstrom lokal teilen: LEG für Eigentümer und Mieter',
        'fr': 'Partager le solaire localement: CEL pour propriétaires et locataires',
        'it': 'Condividere il solare localmente: CEL per proprietari e inquilini',
    },
    'bew_hero_h1': {
        'de': 'Ihr Solarstrom geht an Nachbarn statt ins Netz. Seit dem 1. Januar 2026.',
        'fr': 'Votre énergie solaire va aux voisins au lieu du réseau. Depuis le 1er janvier 2026.',
        'it': 'La vostra energia solare va ai vicini invece che in rete. Dal 1 gennaio 2026.',
    },
    'bew_hero_p': {
        'de': 'Art. 17d StromVG: Hauseigentümer verkaufen Solarstrom direkt an Nachbarn in der Gemeinde, über das bestehende Stromnetz, mit 40% Netzgebühren-Rabatt. Mieter beziehen lokalen Strom, behalten ihre eigene Grundversorgung, haften nur für sich. Kein Verein, kein Verband: eine einfache Gesellschaft mit 1 Monat Kündigungsfrist.',
        'fr': "Art. 17d LApEl: les propriétaires vendent le solaire directement aux voisins dans la commune, via le réseau existant, avec 40% de rabais sur les frais de réseau. Les locataires reçoivent de l'électricité locale, gardent leur approvisionnement de base, ne sont responsables que pour eux. Pas d'association: une société simple avec 1 mois de préavis.",
        'it': 'Art. 17d LAEl: i proprietari vendono il solare direttamente ai vicini nel comune, attraverso la rete esistente, con 40% di sconto sulle tariffe di rete. Gli inquilini ricevono energia locale, mantengono la fornitura di base, rispondono solo per sé. Nessuna associazione: una società semplice con 1 mese di preavviso.',
    },
    'bew_cta_leg_vs_zev': {
        'de': 'LEG vs. ZEV: Was ist der Unterschied?',
        'fr': 'CEL vs. RCP: quelle différence?',
        'it': 'CEL vs. RCP: qual è la differenza?',
    },
    'bew_zahlen_title': {
        'de': 'Konkrete Zahlen: Was LEG-Teilnehmer sparen',
        'fr': 'Chiffres concrets: combien les participants CEL économisent',
        'it': 'Cifre concrete: quanto risparmiano i partecipanti CEL',
    },
    'bew_pv_erloese': {
        'de': 'Mehrerlös für PV-Erzeuger/Jahr',
        'fr': 'Revenu supplémentaire pour producteurs PV/an',
        'it': 'Ricavo aggiuntivo per produttori FV/anno',
    },
    'bew_pv_erloese_detail': {
        'de': "Interner LEG-Preis (10-15 Rp./kWh) vs. Rückspeisevergütung (4-8 Rp./kWh). Bei 5'000 kWh Überschuss: CHF 300-500 mehr pro Jahr.",
        'fr': "Prix CEL interne (10-15 ct./kWh) vs. rémunération de reprise (4-8 ct./kWh). Avec 5'000 kWh d'excédent: CHF 300-500 de plus par an.",
        'it': "Prezzo CEL interno (10-15 ct./kWh) vs. remunerazione di ripresa (4-8 ct./kWh). Con 5'000 kWh di eccedenza: CHF 300-500 in più all'anno.",
    },
    'bew_verbraucher_ersparnis': {
        'de': 'Ersparnis für Verbraucher/Jahr',
        'fr': 'Économies pour les consommateurs/an',
        'it': 'Risparmio per i consumatori/anno',
    },
    'bew_verbraucher_detail': {
        'de': "40% Rabatt auf Netznutzungs-Arbeitspreis (Art. 19h StromVV). Bei H4-Haushalt (4'500 kWh) und 30% internem Verbrauch.",
        'fr': "40% de rabais sur le tarif d'utilisation du réseau (Art. 19h OApEl). Pour un ménage H4 (4'500 kWh) et 30% de consommation interne.",
        'it': "40% di sconto sulla tariffa di utilizzo della rete (Art. 19h OAEl). Per un nucleo H4 (4'500 kWh) e 30% di consumo interno.",
    },
    'bew_netzgebuehr_label': {
        'de': 'Typische Netzgebühr-Einsparung',
        'fr': 'Économie typique sur frais de réseau',
        'it': 'Risparmio tipico sulle tariffe di rete',
    },
    'bew_netzgebuehr_detail': {
        'de': '40% von ~7 Rp./kWh Arbeitspreis. Gilt für jede kWh, die intern in der LEG verbraucht wird.',
        'fr': "40% de ~7 ct./kWh tarif de travail. S'applique à chaque kWh consommé en interne dans la CEL.",
        'it': '40% di ~7 ct./kWh prezzo del lavoro. Si applica a ogni kWh consumato internamente nella CEL.',
    },
    'bew_datenquelle_hinweis': {
        'de': 'Datenquelle: ElCom Strompreisübersicht 2026, StromVV Art. 19h. Noch keine Betriebsdaten aus realen LEGs (erste Aktivierungen: Frühling 2026). Alle Angaben sind Schätzungen.',
        'fr': 'Source: aperçu des prix ElCom 2026, OApEl Art. 19h. Pas encore de données opérationnelles de CEL réelles (premières activations: printemps 2026). Toutes les indications sont des estimations.',
        'it': 'Fonte: panoramica dei prezzi ElCom 2026, OAEl Art. 19h. Nessun dato operativo da CEL reali (prime attivazioni: primavera 2026). Tutti i dati sono stime.',
    },
    'bew_zwei_wege': {
        'de': 'Zwei Wege in die Stromgemeinschaft',
        'fr': 'Deux voies vers la communauté électrique',
        'it': 'Due vie verso la comunità elettrica',
    },
    'bew_eigentuemer_title': {
        'de': 'Eigentümer mit Solaranlage',
        'fr': 'Propriétaire avec installation solaire',
        'it': 'Proprietario con impianto solare',
    },
    'bew_eigentuemer_desc': {
        'de': 'Sie produzieren Solarstrom und speisen den Überschuss ins Netz. Heute erhalten Sie die Rückspeisevergütung (4-8 Rp./kWh). In einer LEG verkaufen Sie direkt an Nachbarn zum internen Preis (10-15 Rp./kWh): doppelter bis dreifacher Erlös pro kWh.',
        'fr': "Vous produisez du solaire et injectez l'excédent dans le réseau. Aujourd'hui vous recevez la rémunération de reprise (4-8 ct./kWh). Dans une CEL, vous vendez directement aux voisins au prix interne (10-15 ct./kWh): revenus doublés ou triplés par kWh.",
        'it': "Producete energia solare e immettete l'eccedenza in rete. Oggi ricevete la remunerazione di ripresa (4-8 ct./kWh). In una CEL, vendete direttamente ai vicini al prezzo interno (10-15 ct./kWh): ricavi doppi o tripli per kWh.",
    },
    'bew_eigentuemer_ertrag': {
        'de': 'Mehr Ertrag:',
        'fr': 'Plus de revenus:',
        'it': 'Più ricavi:',
    },
    'bew_eigentuemer_aufwand': {
        'de': 'Kein Aufwand:',
        'fr': 'Aucun effort:',
        'it': 'Nessuno sforzo:',
    },
    'bew_eigentuemer_haftung': {
        'de': 'Keine Haftung:',
        'fr': 'Aucune responsabilité:',
        'it': 'Nessuna responsabilità:',
    },
    'bew_eigentuemer_smartmeter': {
        'de': 'Smart Meter:',
        'fr': 'Compteur intelligent:',
        'it': 'Smart Meter:',
    },
    'bew_cta_leg_starten': {
        'de': 'LEG starten: Adresse prüfen',
        'fr': "Démarrer une CEL: vérifier l'adresse",
        'it': "Avviare una CEL: verificare l'indirizzo",
    },
    'bew_mieter_title': {
        'de': 'Mieter und Haushalte ohne eigene PV',
        'fr': 'Locataires et ménages sans PV',
        'it': 'Inquilini e famiglie senza FV',
    },
    'bew_mieter_desc': {
        'de': 'Sie beziehen Strom vom lokalen Netz. In einer LEG kommt ein Teil direkt vom Solardach in der Nachbarschaft, günstiger als der Standardtarif. Sie behalten Ihre individuelle Grundversorgung beim VNB, es gibt keine Solidarhaftung.',
        'fr': "Vous consommez de l'électricité du réseau local. Dans une CEL, une partie vient directement du toit solaire du voisinage, moins cher que le tarif standard. Vous gardez votre approvisionnement de base individuel auprès du GRD, pas de responsabilité solidaire.",
        'it': 'Consumate energia dalla rete locale. In una CEL, una parte viene direttamente dal tetto solare del vicinato, più conveniente della tariffa standard. Mantenete la vostra fornitura di base individuale presso il GRD, nessuna responsabilità solidale.',
    },
    'bew_mieter_guenstiger': {
        'de': 'Günstiger Strom:',
        'fr': 'Électricité moins chère:',
        'it': 'Energia più conveniente:',
    },
    'bew_mieter_risiko': {
        'de': 'Kein Risiko:',
        'fr': 'Aucun risque:',
        'it': 'Nessun rischio:',
    },
    'bew_mieter_kuendbar': {
        'de': 'Jederzeit kündbar:',
        'fr': 'Résiliable à tout moment:',
        'it': 'Rescindibile in qualsiasi momento:',
    },
    'bew_mieter_technik': {
        'de': 'Kein Technikaufwand:',
        'fr': 'Aucun effort technique:',
        'it': 'Nessuno sforzo tecnico:',
    },
    'bew_cta_nachbarschaft': {
        'de': 'Nachbarschaft prüfen',
        'fr': 'Vérifier le voisinage',
        'it': 'Verificare il vicinato',
    },
    'bew_gesetz_title': {
        'de': 'Was das Gesetz für Bewohner garantiert',
        'fr': 'Ce que la loi garantit aux habitants',
        'it': 'Cosa garantisce la legge ai residenti',
    },
    'bew_gesetz_rabatt': {
        'de': 'Netzgebühren-Rabatt',
        'fr': 'Rabais sur les frais de réseau',
        'it': 'Sconto sulle tariffe di rete',
    },
    'bew_gesetz_investition': {
        'de': 'CHF Investition',
        'fr': 'CHF investissement',
        'it': 'CHF investimento',
    },
    'bew_gesetz_kuendigung': {
        'de': 'Monat Kündigungsfrist',
        'fr': 'Mois de préavis',
        'it': 'Mese di preavviso',
    },
    'bew_gesetz_haftung': {
        'de': 'Solidarhaftung',
        'fr': 'Responsabilité solidaire',
        'it': 'Responsabilità solidale',
    },
    'bew_leg_nicht_zev': {
        'de': 'LEG ist nicht ZEV. Das ist entscheidend.',
        'fr': "CEL n'est pas RCP. C'est décisif.",
        'it': 'CEL non è RCP. Questo è decisivo.',
    },
    'bew_leg_zev_intro': {
        'de': 'Viele kennen den Zusammenschluss zum Eigenverbrauch (ZEV). Die LEG funktioniert grundlegend anders: weniger Risiko, mehr Reichweite, einfacherer Austritt.',
        'fr': 'Beaucoup connaissent le regroupement pour la consommation propre (RCP). La CEL fonctionne fondamentalement différemment: moins de risque, plus de portée, sortie plus simple.',
        'it': 'Molti conoscono il raggruppamento per il consumo proprio (RCP). La CEL funziona in modo fondamentalmente diverso: meno rischio, più portata, uscita più semplice.',
    },
    'bew_merkmal': {
        'de': 'Merkmal',
        'fr': 'Caractéristique',
        'it': 'Caratteristica',
    },
    'bew_koordination_title': {
        'de': 'Das eigentliche Problem: Koordination',
        'fr': 'Le vrai problème: la coordination',
        'it': 'Il vero problema: il coordinamento',
    },
    'bew_koordination_desc': {
        'de': 'Die Rechtsgrundlage existiert. Der Netzgebühren-Rabatt existiert. Die Herausforderung: genug Nachbarn zusammenbringen, Verträge klären, den Netzbetreiber korrekt informieren.',
        'fr': 'La base légale existe. Le rabais sur les frais de réseau existe. Le défi: réunir assez de voisins, clarifier les contrats, informer correctement le gestionnaire de réseau.',
        'it': 'La base legale esiste. Lo sconto sulle tariffe di rete esiste. La sfida: riunire abbastanza vicini, chiarire i contratti, informare correttamente il gestore di rete.',
    },
    'bew_schritt1_title': {
        'de': '1. Nachbarn finden',
        'fr': '1. Trouver des voisins',
        'it': '1. Trovare i vicini',
    },
    'bew_schritt1_desc': {
        'de': 'Geben Sie Ihre Adresse ein. OpenLEG zeigt, ob in Ihrer Nachbarschaft bereits Haushalte registriert sind und wie hoch das Solarpotenzial liegt.',
        'fr': 'Entrez votre adresse. OpenLEG montre si des ménages sont déjà enregistrés dans votre voisinage et quel est le potentiel solaire.',
        'it': 'Inserite il vostro indirizzo. OpenLEG mostra se nel vostro vicinato sono già registrati nuclei familiari e qual è il potenziale solare.',
    },
    'bew_schritt2_title': {
        'de': '2. Verträge generieren',
        'fr': '2. Générer les contrats',
        'it': '2. Generare i contratti',
    },
    'bew_schritt2_desc': {
        'de': 'Sobald genug Teilnehmer beisammen sind, erstellt OpenLEG die LEG-Vereinbarung, Teilnehmerverträge und das VNB-Anmeldepaket automatisch.',
        'fr': "Dès que suffisamment de participants sont réunis, OpenLEG génère automatiquement l'accord CEL, les contrats de participation et le dossier d'inscription au GRD.",
        'it': "Non appena ci sono abbastanza partecipanti, OpenLEG genera automaticamente l'accordo CEL, i contratti di partecipazione e il pacchetto di iscrizione al GRD.",
    },
    'bew_schritt3_title': {
        'de': '3. VNB anmelden',
        'fr': '3. Inscrire auprès du GRD',
        'it': '3. Iscriversi presso il GRD',
    },
    'bew_schritt3_desc': {
        'de': 'Der Netzbetreiber hat 15 Arbeitstage Antwortfrist und 3 Monate für die Smart-Meter-Installation. OpenLEG trackt den Status.',
        'fr': "Le gestionnaire de réseau a 15 jours ouvrables pour répondre et 3 mois pour l'installation du compteur intelligent. OpenLEG suit le statut.",
        'it': "Il gestore di rete ha 15 giorni lavorativi per rispondere e 3 mesi per l'installazione dello smart meter. OpenLEG monitora lo stato.",
    },
    'bew_sparpotenzial_title': {
        'de': 'Einsparpotenzial in Ihrer Gemeinde',
        'fr': "Potentiel d'économies dans votre commune",
        'it': 'Potenziale di risparmio nel vostro comune',
    },
    'bew_sparpotenzial_desc': {
        'de': 'Die Einsparung hängt vom lokalen Netzgebührentarif ab. Geben Sie Ihre Gemeinde ein und sehen Sie das konkrete Potenzial basierend auf öffentlichen ElCom-Daten.',
        'fr': 'Les économies dépendent du tarif local de frais de réseau. Entrez votre commune et consultez le potentiel concret basé sur les données publiques ElCom.',
        'it': 'Il risparmio dipende dalla tariffa locale delle spese di rete. Inserite il vostro comune e consultate il potenziale concreto basato sui dati pubblici ElCom.',
    },
    'bew_adresse_title': {
        'de': 'Adresse prüfen und registrieren',
        'fr': "Vérifier et enregistrer l'adresse",
        'it': "Verificare e registrare l'indirizzo",
    },
    'bew_adresse_count': {
        'de': 'Bereits {count} Haushalte registriert.',
        'fr': 'Déjà {count} ménages enregistrés.',
        'it': 'Già {count} nuclei familiari registrati.',
    },
    'bew_adresse_hint': {
        'de': 'Wir prüfen, ob in Ihrer Nachbarschaft bereits eine Stromgemeinschaft entsteht.',
        'fr': 'Nous vérifions si une communauté électrique se forme déjà dans votre voisinage.',
        'it': 'Verifichiamo se nel vostro vicinato si sta già formando una comunità elettrica.',
    },
    'bew_daten_title': {
        'de': 'Ihre Daten, Ihre Kontrolle',
        'fr': 'Vos données, votre contrôle',
        'it': 'I vostri dati, il vostro controllo',
    },
    'bew_daten_speichern': {
        'de': 'Was wir speichern',
        'fr': 'Ce que nous stockons',
        'it': 'Cosa salviamo',
    },
    'bew_daten_speichern_desc': {
        'de': 'Adresse (für Nachbarschafts-Matching), E-Mail (für Updates), Einwilligungen (nachweisbar). Keine Smart-Meter-Daten ohne Ihre explizite Freigabe.',
        'fr': 'Adresse (pour le matching de voisinage), e-mail (pour les mises à jour), consentements (vérifiables). Pas de données de compteur intelligent sans votre autorisation explicite.',
        'it': 'Indirizzo (per il matching di vicinato), e-mail (per gli aggiornamenti), consensi (verificabili). Nessun dato smart meter senza la vostra autorizzazione esplicita.',
    },
    'bew_daten_nie': {
        'de': 'Was wir nie tun',
        'fr': 'Ce que nous ne faisons jamais',
        'it': 'Cosa non facciamo mai',
    },
    'bew_daten_nie_desc': {
        'de': 'Kein Datenverkauf an Energieversorger, Versicherungen oder Dritte. Smart-Meter-Daten bleiben in der LEG. Quellcode öffentlich einsehbar (AGPL-3.0). Hosting: Schweizer Infrastruktur (Infomaniak), nDSG-konform.',
        'fr': "Aucune vente de données aux fournisseurs d'énergie, assurances ou tiers. Les données du compteur intelligent restent dans la CEL. Code source consultable publiquement (AGPL-3.0). Hébergement: infrastructure suisse (Infomaniak), conforme nLPD.",
        'it': 'Nessuna vendita di dati a fornitori di energia, assicurazioni o terzi. I dati dello smart meter restano nella CEL. Codice sorgente consultabile pubblicamente (AGPL-3.0). Hosting: infrastruttura svizzera (Infomaniak), conforme nLPD.',
    },
    'bew_rechtsgrundlagen': {
        'de': 'Rechtsgrundlagen und Quellen',
        'fr': 'Bases légales et sources',
        'it': 'Basi legali e fonti',
    },
    'bew_angebot_gemeinden': {
        'de': 'Angebot für Gemeinden',
        'fr': 'Offre pour les communes',
        'it': 'Offerta per i comuni',
    },
    # === VNB Transparency page (15 keys) ===
    'transparenz_title': {
        'de': 'VNB-Transparenz: Wie offen publizieren Schweizer Netzbetreiber?',
        'fr': 'Transparence GRD: comment les gestionnaires de réseau suisses publient-ils?',
        'it': 'Trasparenza GRD: come pubblicano i gestori di rete svizzeri?',
    },
    'transparenz_subtitle': {
        'de': 'Bewertung der Tarifdaten-Vollständigkeit aller Schweizer Verteilnetzbetreiber. Basierend auf öffentlichen ElCom-Daten.',
        'fr': 'Évaluation de la complétude des données tarifaires de tous les gestionnaires de réseau suisses. Basé sur les données publiques ElCom.',
        'it': 'Valutazione della completezza dei dati tariffari di tutti i gestori di rete svizzeri. Basato sui dati pubblici ElCom.',
    },
    'transparenz_meta_desc': {
        'de': 'Transparenz-Ranking Schweizer Verteilnetzbetreiber (VNB). Bewertung 0-100 basierend auf ElCom-Tarifdaten. Offene Daten.',
        'fr': 'Classement de transparence des gestionnaires de réseau suisses (GRD). Score 0-100 basé sur les données tarifaires ElCom.',
        'it': 'Classifica di trasparenza dei gestori di rete svizzeri (GRD). Punteggio 0-100 basato sui dati tariffari ElCom.',
    },
    'transparenz_filter_kanton': {
        'de': 'Kanton',
        'fr': 'Canton',
        'it': 'Cantone',
    },
    'transparenz_alle_kantone': {
        'de': 'Alle Kantone',
        'fr': 'Tous les cantons',
        'it': 'Tutti i cantoni',
    },
    'transparenz_col_vnb': {
        'de': 'Netzbetreiber (VNB)',
        'fr': 'Gestionnaire de réseau (GRD)',
        'it': 'Gestore di rete (GRD)',
    },
    'transparenz_col_score': {
        'de': 'Score',
        'fr': 'Score',
        'it': 'Score',
    },
    'transparenz_col_gemeinden': {
        'de': 'Gemeinden',
        'fr': 'Communes',
        'it': 'Comuni',
    },
    'transparenz_col_status': {
        'de': 'Status',
        'fr': 'Statut',
        'it': 'Stato',
    },
    'transparenz_status_gut': {
        'de': 'Gute Datenlage',
        'fr': 'Bonnes données',
        'it': 'Buoni dati',
    },
    'transparenz_status_luecken': {
        'de': 'Datenlücken vorhanden',
        'fr': 'Lacunes dans les données',
        'it': 'Lacune nei dati',
    },
    'transparenz_status_mangelhaft': {
        'de': 'Mangelhafte Transparenz',
        'fr': 'Transparence insuffisante',
        'it': 'Trasparenza insufficiente',
    },
    'transparenz_elcom_melden': {
        'de': 'ElCom melden',
        'fr': 'Signaler à ElCom',
        'it': 'Segnalare a ElCom',
    },
    'transparenz_keine_daten': {
        'de': 'Keine Tarifdaten verfügbar. Daten werden über den Cron-Job aktualisiert.',
        'fr': 'Aucune donnée tarifaire disponible. Les données sont mises à jour par le cron.',
        'it': 'Nessun dato tariffario disponibile. I dati vengono aggiornati tramite cron.',
    },
    'transparenz_methodik': {
        'de': 'Methodik: Score 0-100. Gewichtung: Kategorie-Abdeckung (30%), Komponenten-Vollständigkeit (30%), Gemeinde-Abdeckung (20%), Datenpräsenz (20%).',
        'fr': 'Méthodologie: Score 0-100. Pondération: couverture des catégories (30%), complétude des composantes (30%), couverture des communes (20%), présence des données (20%).',
        'it': 'Metodologia: Score 0-100. Ponderazione: copertura delle categorie (30%), completezza delle componenti (30%), copertura dei comuni (20%), presenza dei dati (20%).',
    },
    'transparenz_datenquelle': {
        'de': 'Datenquelle: ElCom LINDAS SPARQL Endpoint (lindas.admin.ch). Aktualisierung: jährlich.',
        'fr': 'Source: ElCom LINDAS SPARQL Endpoint (lindas.admin.ch). Mise à jour: annuelle.',
        'it': 'Fonte: ElCom LINDAS SPARQL Endpoint (lindas.admin.ch). Aggiornamento: annuale.',
    },
    'transparenz_letzte_aktualisierung': {
        'de': 'Letzte Aktualisierung',
        'fr': 'Dernière mise à jour',
        'it': 'Ultimo aggiornamento',
    },
    'transparenz_datensaetze': {
        'de': 'Datensätze',
        'fr': 'enregistrements',
        'it': 'record',
    },
    'transparenz_stats_vnb': {
        'de': 'Netzbetreiber erfasst',
        'fr': 'Gestionnaires de réseau enregistrés',
        'it': 'Gestori di rete registrati',
    },
    'transparenz_stats_avg': {
        'de': 'Durchschnittlicher Score',
        'fr': 'Score moyen',
        'it': 'Score medio',
    },
    'transparenz_stats_gemeinden': {
        'de': 'Gemeinden abgedeckt',
        'fr': 'Communes couvertes',
        'it': 'Comuni coperti',
    },
    # === Gemeinde Toolkit page (8 keys) ===
    'toolkit_title': {
        'de': 'Gemeinde-Kit: Materialien für Ihre LEG-Kommunikation',
        'fr': 'Kit communal: matériels pour votre communication CEL',
        'it': 'Kit comunale: materiali per la vostra comunicazione CEL',
    },
    'toolkit_subtitle': {
        'de': 'Fertige Vorlagen für Gemeindeblatt, Poster, Gemeinderat-Info und Social Media. Frei nutzbar, anpassbar.',
        'fr': "Modèles prêts à l'emploi pour le bulletin communal, affiches, info pour le conseil communal et réseaux sociaux. Libres d'utilisation, adaptables.",
        'it': 'Modelli pronti per il bollettino comunale, poster, info per il consiglio comunale e social media. Liberi di utilizzo, adattabili.',
    },
    'toolkit_gemeindeblatt': {
        'de': 'Gemeindeblatt-Inserat (A5 PDF)',
        'fr': 'Encart bulletin communal (A5 PDF)',
        'it': 'Inserto bollettino comunale (A5 PDF)',
    },
    'toolkit_poster': {
        'de': 'Poster mit QR-Code (A3 PDF)',
        'fr': 'Affiche avec code QR (A3 PDF)',
        'it': 'Poster con codice QR (A3 PDF)',
    },
    'toolkit_gemeinderat': {
        'de': 'Gemeinderat-Infoblatt',
        'fr': "Fiche d'information conseil communal",
        'it': 'Scheda informativa consiglio comunale',
    },
    'toolkit_social': {
        'de': 'Social-Media-Textvorlagen',
        'fr': 'Modèles de texte réseaux sociaux',
        'it': 'Modelli di testo social media',
    },
    'toolkit_logo': {
        'de': 'Logo-Assets (SVG, PNG)',
        'fr': 'Ressources logo (SVG, PNG)',
        'it': 'Risorse logo (SVG, PNG)',
    },
    'toolkit_download': {
        'de': 'Herunterladen',
        'fr': 'Télécharger',
        'it': 'Scaricare',
    },
    'toolkit_gemeindeblatt_desc': {
        'de': 'A5 Inserat für das Gemeindeblatt. Enthält QR-Code zur Registrierung, Sparpotenzial-Zahlen, Rechtsgrundlage.',
        'fr': "Annonce A5 pour le bulletin communal. Contient un QR-Code d'inscription, le potentiel d'économies et la base légale.",
        'it': 'Inserto A5 per il bollettino comunale. Contiene codice QR per la registrazione, potenziale di risparmio e base giuridica.',
    },
    'toolkit_poster_desc': {
        'de': 'A3 Poster mit grossem QR-Code. Für Gemeindehaus, Bibliothek, Quartiertreffpunkt.',
        'fr': 'Affiche A3 avec grand QR-Code. Pour la mairie, la bibliothèque, le centre de quartier.',
        'it': 'Poster A3 con grande codice QR. Per il municipio, la biblioteca, il centro di quartiere.',
    },
    'toolkit_gemeinderat_desc': {
        'de': 'Zweiseitiges Infoblatt für Gemeinderäte. Rechtsgrundlage, Vorteile, Verantwortlichkeiten, OpenLEG-Angebot.',
        'fr': "Fiche d'information de deux pages pour les conseillers municipaux. Base légale, avantages, responsabilités, offre OpenLEG.",
        'it': 'Scheda informativa di due pagine per i consiglieri comunali. Base giuridica, vantaggi, responsabilità, offerta OpenLEG.',
    },
    'toolkit_social_desc': {
        'de': 'Fertige Textvorlagen für Facebook, Instagram, LinkedIn. Kopieren, anpassen, posten.',
        'fr': "Modèles de texte prêts à l'emploi pour Facebook, Instagram, LinkedIn. Copier, adapter, publier.",
        'it': 'Modelli di testo pronti per Facebook, Instagram, LinkedIn. Copiare, adattare, pubblicare.',
    },
    'toolkit_logo_desc': {
        'de': 'OpenLEG Logo in SVG und PNG. Für Flyer, Websites, Präsentationen. Freie Nutzung.',
        'fr': 'Logo OpenLEG en SVG et PNG. Pour flyers, sites web, présentations. Utilisation libre.',
        'it': 'Logo OpenLEG in SVG e PNG. Per volantini, siti web, presentazioni. Uso libero.',
    },
    'toolkit_foerder_title': {
        'de': 'Fördermittel für Ihre Gemeinde',
        'fr': 'Subventions pour votre commune',
        'it': 'Sovvenzioni per il vostro comune',
    },
    'toolkit_foerder_subtitle': {
        'de': 'Nutzen Sie bestehende Förderprogramme, um die LEG-Gründung zu finanzieren.',
        'fr': 'Utilisez les programmes de subventions existants pour financer la création de votre CEL.',
        'it': 'Utilizzate i programmi di sovvenzione esistenti per finanziare la creazione della vostra CEL.',
    },
    'toolkit_foerder_energieschweiz': {
        'de': 'EnergieSchweiz',
        'fr': 'SuisseEnergie',
        'it': 'SvizzeraEnergia',
    },
    'toolkit_foerder_energieschweiz_desc': {
        'de': 'Bundesbeiträge für kommunale Energieberatung und Umsetzungsprojekte. Unterstützt Gemeinden bei der Planung von LEGs.',
        'fr': 'Contributions fédérales pour le conseil énergétique communal et les projets de mise en œuvre. Soutient les communes dans la planification des CEL.',
        'it': 'Contributi federali per la consulenza energetica comunale e progetti di attuazione. Sostiene i comuni nella pianificazione delle CEL.',
    },
    'toolkit_foerder_pronovo': {
        'de': 'Pronovo (Einmalvergütung)',
        'fr': 'Pronovo (rétribution unique)',
        'it': 'Pronovo (rimunerazione unica)',
    },
    'toolkit_foerder_pronovo_desc': {
        'de': 'PV-Investitionsbeiträge für Gebäudeeigentümer. Senkt die Kosten für Solaranlagen in der LEG.',
        'fr': "Contributions d'investissement PV pour les propriétaires. Réduit les coûts des installations solaires dans la CEL.",
        'it': "Contributi d'investimento PV per i proprietari. Riduce i costi degli impianti solari nella CEL.",
    },
    'toolkit_foerder_kantonal': {
        'de': 'Kantonale Programme',
        'fr': 'Programmes cantonaux',
        'it': 'Programmi cantonali',
    },
    'toolkit_foerder_kantonal_desc': {
        'de': 'Jeder Kanton bietet eigene Energieförderprogramme. Finden Sie die Angebote Ihres Kantons.',
        'fr': 'Chaque canton propose ses propres programmes de promotion énergétique. Trouvez les offres de votre canton.',
        'it': 'Ogni cantone offre propri programmi di promozione energetica. Trovate le offerte del vostro cantone.',
    },
}


def t(key, lang='de'):
    """Look up translation. Falls back: requested lang -> de -> key itself."""
    entry = TRANSLATIONS.get(key, {})
    return entry.get(lang) or entry.get('de') or key
