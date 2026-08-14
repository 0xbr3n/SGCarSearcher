"""A much larger built-in reference list of Singapore-market car models,
browsable by brand in the /hunt wizard. It will never cover everything —
that's what the "Type a model" free-text option in the wizard is for
(e.g. an E63 AMG, or any rare/specific trim not worth cataloguing).
"""

CATALOG = {
    "Toyota": ["Corolla Altis", "Camry", "Vios", "Yaris", "Wish", "Estima", "Alphard", "Vellfire", "Harrier", "RAV4", "C-HR", "Prius", "86"],
    "Honda": ["Civic", "City", "Vezel", "CR-V", "Jazz", "Odyssey", "Accord", "HR-V", "Fit", "Freed", "Shuttle"],
    "Mazda": ["3", "2", "6", "CX-5", "CX-3", "CX-30", "CX-8", "MX-5"],
    "Nissan": ["Sylphy", "Almera", "Qashqai", "X-Trail", "Note", "Serena", "Latio", "Juke"],
    "Hyundai": ["Avante", "Elantra", "Tucson", "i30", "Kona", "Ioniq", "Santa Fe"],
    "Kia": ["Cerato", "Forte", "Sportage", "Stonic", "Picanto", "Rio", "Niro"],
    "Lexus": ["IS300", "ES250", "RX300", "NX300", "UX200", "LS500", "LC500"],
    "BMW": ["1 Series", "3 Series", "5 Series", "X1", "X3", "X5", "M3", "M4", "M340i", "M5"],
    "Mercedes-Benz": ["A-Class", "C-Class", "E-Class", "S-Class", "GLA", "GLC", "C63 AMG", "E63 AMG", "A45 AMG", "GLC63 AMG"],
    "Audi": ["A3", "A4", "A6", "Q3", "Q5", "S3", "RS3", "RS6"],
    "Volkswagen": ["Golf", "Polo", "Passat", "Tiguan", "Jetta", "Arteon"],
    "Volvo": ["S60", "V40", "XC40", "XC60", "XC90"],
    "Subaru": ["XV", "Forester", "WRX", "Impreza", "Levorg", "BRZ"],
    "Mitsubishi": ["Attrage", "Lancer", "ASX", "Outlander", "Mirage"],
    "Peugeot": ["208", "308", "2008", "3008", "5008"],
    "Porsche": ["Cayenne", "Macan", "Panamera", "911", "Taycan"],
}

BRANDS = list(CATALOG.keys())
