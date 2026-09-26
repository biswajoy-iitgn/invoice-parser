"""Output schema, charge categories, and the synonym glossary used for semantic mapping."""

HEADER_FIELDS = [
    "Invoice No",
    "Invoice Date",
    "Container Number",
    "MAWB No",
    "HAWB No",
    "Vendor Name",
    "Bill of Lading / HBL",
    "Weight",
]

# Output order follows the order the fields were requested in.
OUTPUT_FIELDS = [
    "Invoice No",
    "Invoice Date",
    "Container Number",
    "MAWB No",
    "HAWB No",
    "Vendor Name",
    "Bill of Lading / HBL",
    "Weight",
    "Sea Freight",
    "Air Freight",
    "Terminal Handling Charges",
    "Airway Bill Charges",
    "BL Charges",
    "ENS Charges",
    "Seal Charges",
    "VGM",
    "EDI Fees",
    "DAP Charges",
    "Other Charges",
    "AMS Charges",
    "Handling Charges",
    "Pre Carriage Haulage",
    "Ex Rate",
    "Misc",
    "Transportation",
    "Warehouse Charges Port",
    "Examination Charges",
    "Fuel Charges",
    "Cargo Screening",
    "Agency Charges",
    "Shipping Bill Charges",
]

# Each charge category: meaning (fed to the LLM) and high-precision regex synonyms
# (used as a deterministic cross-check). Regexes are matched case-insensitively
# against the line-item description.
CHARGE_CATEGORIES = {
    "Sea Freight": {
        "meaning": "Ocean/sea freight for carrying cargo port to port (FCL/LCL). "
        "Aliases: sea freight, ocean freight, seafreight, sea export freight, freight (on a sea shipment), O/F.",
        "patterns": [r"sea\s*-?\s*freight", r"ocean\s+freight", r"sea\s+export\s+freight", r"\bO/F\b"],
    },
    "Air Freight": {
        "meaning": "Air freight for carrying cargo airport to airport. "
        "Aliases: air freight, airfreight, air export freight, freight (on an air shipment), A/F.",
        "patterns": [r"air\s*-?\s*freight", r"\bA/F\b"],
    },
    "Terminal Handling Charges": {
        "meaning": "Terminal / port handling charges at origin or destination terminal. "
        "Aliases: THC, terminal handling, THC origin, OTHC, DTHC, THC port of loading.",
        "patterns": [r"\bO?D?THC\b", r"terminal\s+handling"],
    },
    "Airway Bill Charges": {
        "meaning": "Fee for issuing the air waybill. Aliases: AWB fee, airway bill charges, air waybill fee, MAWB/HAWB fee.",
        "patterns": [r"\bAWB\s+(fee|charge)", r"air\s*way\s*bill", r"airwaybill", r"\b[MH]AWB\s+(fee|charge)"],
    },
    "BL Charges": {
        "meaning": "Fee for issuing the Bill of Lading / documentation. "
        "Aliases: B/L fee, BL fees, bill of lading charges, documentation fee, doc fee, SWB fee.",
        "patterns": [r"\bB/?L\s+(fee|charge)", r"\bBL\s+FEES?\b", r"bill\s+of\s+lading", r"^\s*B/L\b",
                     r"documentation\s+(fee|charge)", r"\bdoc(s)?\s+fee"],
    },
    "ENS Charges": {
        "meaning": "EU Entry Summary Declaration (ENS / ICS2) security filing for cargo to Europe. "
        "Aliases: ENS charge, ENS filing, ICS2, entry summary declaration. "
        "Generic 'security manifest' filing on a Europe-bound shipment also belongs here.",
        "patterns": [r"\bENS\b", r"\bICS\s*2\b", r"entry\s+summary"],
    },
    "Seal Charges": {
        "meaning": "Container seal fee. Aliases: seal fee, seal charges, seal, e-seal, bolt seal.",
        "patterns": [r"\bseal\b"],
    },
    "VGM": {
        "meaning": "Verified Gross Mass (SOLAS container weighing) certificate/fee. Aliases: VGM, verified gross mass, weighment.",
        "patterns": [r"\bVGM\b", r"verified\s+gross\s+mass", r"weighment"],
    },
    "EDI Fees": {
        "meaning": "Electronic data interchange / e-filing fees. Aliases: EDI fee, EDI charges, e-manifest filing, IGM/EGM filing.",
        "patterns": [r"\bEDI\b", r"\b[IE]GM\s+filing"],
    },
    "DAP Charges": {
        "meaning": "Destination charges billed because of DAP/DDP delivery terms, e.g. 'DAP charges', "
        "destination delivery charges, delivery to consignee door at destination.",
        "patterns": [r"\bDAP\b", r"\bDDP\b", r"destination\s+delivery", r"\bdelivery\s+charges?\b"],
    },
    "AMS Charges": {
        "meaning": "US Automated Manifest System (AMS) / ISF security filing for cargo to the USA. "
        "Aliases: AMS fee, AMS security fee, ISF, ACI (Canada). Generic 'security manifest' filing on a USA-bound shipment belongs here.",
        "patterns": [r"\bAMS\b", r"\bISF\b", r"\bACI\b"],
    },
    "Handling Charges": {
        "meaning": "Cargo handling other than terminal handling: loading/unloading, labour, handling import/export, "
        "stuffing/destuffing, forwarder handling fee.",
        "patterns": [r"(?<!terminal )handling", r"loading\s*(&|and)\s*unloading", r"\blabou?r\b", r"stuffing"],
    },
    "Pre Carriage Haulage": {
        "meaning": "Inland transport from shipper / ICD to the port of loading BEFORE the main voyage. "
        "Aliases: pre-carriage, inland haulage charges (IHC), carrier haulage origin, carrier charges origin, "
        "origin haulage, linehaul to port, rail/ICD haulage to port.",
        "patterns": [r"pre[\s-]*carriage", r"inland\s+haulage", r"\bIHC\b", r"origin\s+haulage", r"carrier\s+charges?\s+origin"],
    },
    "Transportation": {
        "meaning": "Local/domestic trucking not tied to a specific leg above: transportation, local freight, "
        "cartage, trucking, road freight, haulage from port to warehouse, on-carriage.",
        "patterns": [r"transportation", r"local\s+freigh?t", r"cartage", r"trucking", r"on[\s-]*carriage"],
    },
    "Warehouse Charges Port": {
        "meaning": "Storage / warehouse / CFS / ICD / air-cargo complex charges at the port. "
        "Aliases: warehouse charge (port/CFS/ICD/air cargo), CFS charges, ground rent, storage, demurrage at CFS.",
        "patterns": [r"warehous\w*\s+(charges?|fees?|rent)", r"\bCFS\b", r"ground\s+rent", r"storage"],
    },
    "Examination Charges": {
        "meaning": "Customs examination / inspection of cargo. Aliases: examination charges, customs exam, inspection, scanning.",
        "patterns": [r"examination", r"\bexam\b", r"inspection"],
    },
    "Fuel Charges": {
        "meaning": "Fuel / bunker surcharges. Aliases: fuel surcharge, FSC, BAF, bunker adjustment factor, "
        "emergency bunker surcharge (EBS), low sulphur surcharge (LSS).",
        "patterns": [r"\bfuel\b", r"\bFSC\b", r"\bBAF\b", r"bunker", r"\bEBS\b", r"low\s+sulphur", r"\bLSS\b"],
    },
    "Cargo Screening": {
        "meaning": "Security screening / X-ray of (air) cargo. Aliases: screening charges, X-ray, security screening.",
        "patterns": [r"screening", r"x[\s-]*ray"],
    },
    "Agency Charges": {
        "meaning": "Customs broker / clearing agent / agency fee. Aliases: agency, agency charges, clearing charges, "
        "customs clearance, CHA charges, service charge of the agent.",
        "patterns": [r"\bagency\b", r"clearing\s+charges?", r"customs\s+clearance", r"\bCHA\b"],
    },
    "Shipping Bill Charges": {
        "meaning": "Fee for filing/processing the export shipping bill. Aliases: shipping bill charges, "
        "shipping bill passing, SB filing, SB charges.",
        "patterns": [r"shipping\s+bill", r"\bS/?B\s+(filing|charges?|fee)"],
    },
    "Misc": {
        "meaning": "Line items explicitly labelled miscellaneous / sundry / incidental.",
        "patterns": [r"\bmisc", r"sundry", r"incidental"],
    },
    "Other Charges": {
        "meaning": "Any real charge that fits none of the categories above, e.g. ISPS, equipment surcharge, "
        "mandatory user charges, DO fee, T1 document, service fee, SSR charges, carrier surcharges.",
        "patterns": [],
    },
}

CHARGE_FIELDS = list(CHARGE_CATEGORIES)

assert set(OUTPUT_FIELDS) == set(HEADER_FIELDS) | set(CHARGE_FIELDS) | {"Ex Rate"}


def glossary_text() -> str:
    return "\n".join(f"- {name}: {spec['meaning']}" for name, spec in CHARGE_CATEGORIES.items())


LLM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_no": {"type": "string"},
        "invoice_date": {"type": "string"},
        "vendor_name": {"type": "string"},
        "transport_mode": {"type": "string", "enum": ["sea", "air", "road", "unknown"]},
        "destination_country": {"type": "string"},
        "container_numbers": {"type": "array", "items": {"type": "string"}},
        "mawb_no": {"type": "string"},
        "hawb_no": {"type": "string"},
        "master_bl_no": {"type": "string"},
        "house_bl_no": {"type": "string"},
        "gross_weight": {"type": "string"},
        "chargeable_weight": {"type": "string"},
        "exchange_rates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"currency": {"type": "string"}, "rate": {"type": "number"}},
                "required": ["currency", "rate"],
            },
        },
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "amount_inr": {"type": "number"},
                    "category": {"type": "string", "enum": CHARGE_FIELDS},
                },
                "required": ["description", "amount_inr", "category"],
            },
        },
        "subtotal_before_tax": {"type": ["number", "null"]},
        "total_invoice_amount": {"type": ["number", "null"]},
    },
    "required": [
        "invoice_no", "invoice_date", "vendor_name", "transport_mode", "destination_country",
        "container_numbers", "mawb_no", "hawb_no", "master_bl_no", "house_bl_no",
        "gross_weight", "chargeable_weight", "exchange_rates", "line_items",
        "subtotal_before_tax", "total_invoice_amount",
    ],
}
