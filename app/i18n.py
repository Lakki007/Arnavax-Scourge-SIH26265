"""English / Hindi interface text for institution-facing pages and alerts (toggle: /lang/hi, /lang/en).
Officer pages stay in English. Proof bytes and bank values are never translated."""
HI = {
    # navigation and chrome
    "My deposits": "मेरी जमा राशियाँ", "Alerts": "सूचनाएँ", "Mock bank": "नमूना बैंक", "System": "सिस्टम",
    "How it works": "यह कैसे काम करता है", "Sign out": "साइन आउट", "Institution": "संस्थान", "हिन्दी": "English",
    # portal
    "Security deposits held for AICTE.": "एआईसीटीई के पास रखी गई सुरक्षा जमा राशियाँ।",
    "When AICTE asks for a bank proof, answer it from your own net-banking session. AICTE's verifier sees only the deposit record, never your password, cookies, PAN or other accounts.":
        "जब एआईसीटीई बैंक प्रमाण माँगे, तो अपने ही नेट-बैंकिंग सत्र से उत्तर दें। एआईसीटीई का सत्यापक केवल जमा का रिकॉर्ड देखता है — कभी आपका पासवर्ड, कुकीज़, पैन या अन्य खाते नहीं।",
    "File a new FDR": "नई एफडीआर दर्ज करें",
    "Security deposit (AICTE APH Table 1.4):": "सुरक्षा जमा (एआईसीटीई एपीएच तालिका 1.4):",
    "required": "आवश्यक", "filed": "जमा की गई", "bank-confirmed": "बैंक द्वारा पुष्ट",
    "Shortfall": "कमी", "Adequate as filed": "जमा की गई राशि पर्याप्त है", "All alerts": "सभी सूचनाएँ",
    "Open requests from AICTE": "एआईसीटीई के खुले अनुरोध",
    "AICTE asks your bank's server to confirm this deposit.": "एआईसीटीई आपके बैंक के सर्वर से इस जमा की पुष्टि माँगता है।",
    "Prove from my bank": "मेरे बैंक से प्रमाणित करें",
    "No open requests.": "कोई खुला अनुरोध नहीं।",
    "Registered deposits": "पंजीकृत जमा राशियाँ", "FD number": "एफडी संख्या", "Principal": "मूलधन", "Matures": "परिपक्वता",
    "Status at AICTE": "एआईसीटीई में स्थिति", "Vouched for by": "किसके द्वारा प्रमाणित",
    # alerts page
    "Reminders and escalations, generated daily.": "अनुस्मारक और एस्केलेशन, प्रतिदिन बनाए जाते हैं।",
    "When": "कब", "Alert": "सूचना", "Deposit": "जमा", "To": "प्राप्तकर्ता", "Message": "संदेश", "No alerts yet.": "अभी कोई सूचना नहीं।",
    # alert titles
    "Maturity reminder T-90": "परिपक्वता अनुस्मारक (90 दिन शेष)", "Maturity reminder T-60": "परिपक्वता अनुस्मारक (60 दिन शेष)",
    "Maturity reminder T-30": "परिपक्वता अनुस्मारक (30 दिन शेष)", "Maturity reminder T-7": "परिपक्वता अनुस्मारक (7 दिन शेष)",
    "Bank re-check due": "बैंक से पुनः पुष्टि देय", "Security deposit shortfall": "सुरक्षा जमा में कमी",
    "Deposit term shorter than 10 years": "जमा अवधि 10 वर्ष से कम",
    # statuses and provenance
    "Submitted by institution": "संस्थान द्वारा प्रस्तुत", "Proof requested": "प्रमाण माँगा गया", "Verified, awaiting officer": "सत्यापित, अधिकारी की प्रतीक्षा",
    "Confirmed by bank session": "बैंक सत्र द्वारा पुष्ट", "Confirmed by bank branch": "बैंक शाखा द्वारा पुष्ट", "Discrepancy": "विसंगति",
    "Insufficient evidence": "अपर्याप्त प्रमाण", "Rejected": "अस्वीकृत", "Escalated": "उच्च स्तर पर भेजा गया", "Confirmation expired": "पुष्टि की अवधि समाप्त",
    "On hold": "रोक लगी है", "Release proposed": "रिलीज़ प्रस्तावित", "Renewed": "नवीनीकृत", "Released": "रिलीज़ की गई",
    "Paper only": "केवल कागज़", "Bank's server": "बैंक का सर्वर", "Bank branch (tier 3)": "बैंक शाखा (स्तर 3)", "Bank contradicts": "बैंक विरोधाभास दिखाता है",
    "Paper; last proof failed": "कागज़; पिछला प्रमाण विफल", "Paper; bank confirmation expired": "कागज़; बैंक पुष्टि समाप्त",
    "No open requests. AICTE's officer creates one from the register. In the demo, sign in as the officer and choose \"Request bank proof\" on a deposit.":
        "कोई खुला अनुरोध नहीं। एआईसीटीई अधिकारी रजिस्टर से अनुरोध बनाता है। डेमो में अधिकारी के रूप में साइन इन करें और किसी जमा पर \"Request bank proof\" चुनें।",
}

HI_ALERT_BODY = {
    "maturity_reminder": "{fd} की परिपक्वता निकट है। नवीनीकृत एफडीआर दर्ज करें और नए बैंक प्रमाण का उत्तर दें।",
    "recheck_due": "{fd} की कोई वर्तमान बैंक पुष्टि नहीं है। एआईसीटीई का प्रमाण अनुरोध आने पर उत्तर दें।",
    "deposit_shortfall": "आपकी सुरक्षा जमा एआईसीटीई एपीएच तालिका 1.4 की आवश्यकता से कम है। शेष राशि जमा करें।",
    "term_gap": "{fd} 10 वर्ष की अवधि पूरी होने से पहले परिपक्व होती है। परिपक्वता से पहले नवीनीकरण करें।",
}


def translator(lang):
    if lang != "hi":
        return lambda en: en
    return lambda en: HI.get(en, en)
