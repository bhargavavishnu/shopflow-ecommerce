import random
from django.contrib.postgres.search import SearchVector
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from store.models import Category, Inventory, Product

CATALOG_TEMPLATES = [
    (
        "Laptops & Workstations",
        [
            "ProBook Mobile Workstation",
            "AeroBlade Carbon Ultrabook",
            "StudioRack Render Laptop",
            "DevForge Linux Laptop",
        ],
        [
            "aerospace aluminum chassis with 100W USB-C power delivery, PCIe Gen4 NVMe throughput, calibrated mini-LED display, and whisper-quiet vapor chamber cooling for software compilation and neural workloads.",
            "long-battery ultraportable engineering workstation featuring Wi-Fi 7 tri-band radio, hardware TPM security enclave, spill-resistant tactile keyboard, and Thunderbolt 4 dock compatibility.",
        ],
        (89900, 299900),
    ),
    (
        "Mechanical Keyboards",
        [
            "Tactile75 Hot-Swap Keyboard",
            "SplitErgo Programmable Board",
            "SilentLinear 80% Mechanical",
            "BrassPlate Studio Keyboard",
        ],
        [
            "gasket-mounted aluminum keyboard with QMK/VIA firmware programmable layers, south-facing per-key switches, PBT dye-sub keycaps, and multi-device Bluetooth + wired USB-C polling.",
            "ergonomic low-latency developer keyboard featuring factory-lubed tactile switches, acoustic Poron dampening foam, rotary aluminum encoder, and detachable coiled braided cable.",
        ],
        (7900, 32900),
    ),
    (
        "Audio & Studio",
        [
            "ReferencePlanar Studio Headphones",
            "ClearCast Cardioid Condenser Mic",
            "DeskDAC Balanced Amp",
            "NearField Reference Monitors",
        ],
        [
            "planar magnetic open-back transducer engineered for flat studio mixing, ultra-low harmonic distortion, breathable velour earpads, and balanced XLR audio interconnect.",
            "broadcast dynamic USB/XLR microphone with zero-latency headphone monitoring, shock-mount isolation arm compatibility, and clean DSP noise gate circuitry.",
        ],
        (6900, 89900),
    ),
    (
        "Networking & Homelab",
        [
            "MultiGig 10GbE Managed Switch",
            "RackNAS 8-Bay Storage Chassis",
            "MeshWiFi 7 Tri-Band Router",
            "PoE++ rackmount Patch Injector",
        ],
        [
            "enterprise SFP+ 10-gigabit managed Layer-2/3 rackmount switch with VLAN segmentation, link aggregation LACP, redundant fans, and quiet homelab thermal profile.",
            "modular NAS server enclosure supporting ZFS ECC memory, hot-swap SATA/SAS trays, dual 2.5GbE failover NICs, and local snapshot backup automation.",
        ],
        (11900, 129900),
    ),
    (
        "Storage & Memory",
        [
            "NVMe Pro PCIe Gen5 SSD",
            "DDR5 ECC Workstation Kit",
            "Rugged Thunderbolt Portable Drive",
            "Enterprise RAID Cache Module",
        ],
        [
            "sequential read speeds exceeding 12,000 MB/s with dynamic SLC write cache, anodized graphene heat spreader, hardware AES-256 encryption, and sustained database IOPS stability.",
            "IP67 dust and waterproof aluminum portable SSD designed for 8K RAW video scratch editing, direct camera offloading, and fast encrypted incremental backups.",
        ],
        (4900, 54900),
    ),
    (
        "Monitors & Displays",
        [
            "UltraSharp 5K Designer Monitor",
            "OLED 34-Inch Ultrawide Curved",
            "Dual-4K DaisyChain Display",
            "Portable IPS USB-C Travel Screen",
        ],
        [
            "factory-calibrated 99% DCI-P3 color gamut monitor with 96W USB-C single-cable hub, integrated KVM switch, ergonomic height stand, and anti-glare matte finish.",
            "144Hz OLED ultrawide productivity panel with deep true blacks, hardware low-blue-light filter, picture-by-picture split inputs, and VESA mount compatibility.",
        ],
        (19900, 169900),
    ),
]

SERIES_ADJECTIVES = [
    "Edition",
    "Plus",
    "Pro",
    "Max",
    "Compact",
    "Studio",
    "Field",
    "Core",
    "Enterprise",
    "Signature",
]


class Command(BaseCommand):
    help = "Seed ~1,000 realistic hardware & developer store products with inventory and GIN search vectors"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=1000)
        parser.add_argument("--reset", action="store_true")

    def handle(self, *args, **options):
        count = options["count"]
        rng = random.Random(42)

        with transaction.atomic():
            if options["reset"]:
                Inventory.objects.all().delete()
                Product.objects.all().delete()
                Category.objects.all().delete()

            existing_count = Product.objects.count()
            if existing_count >= count and not options["reset"]:
                self.stdout.write(
                    self.style.SUCCESS(f"Already have {existing_count} products seeded.")
                )
                return

            categories = {}
            for cat_name, *_ in CATALOG_TEMPLATES:
                cat, _ = Category.objects.get_or_create(
                    slug=slugify(cat_name), defaults={"name": cat_name}
                )
                categories[cat_name] = cat

            products_to_create = []
            start_idx = existing_count + 1
            for idx in range(start_idx, count + 1):
                cat_name, names, descriptions, (low_p, high_p) = CATALOG_TEMPLATES[
                    (idx - 1) % len(CATALOG_TEMPLATES)
                ]
                base_name = names[(idx // len(CATALOG_TEMPLATES)) % len(names)]
                adj = SERIES_ADJECTIVES[(idx * 7) % len(SERIES_ADJECTIVES)]
                gen = (idx % 5) + 1
                name = f"{base_name} {adj} G{gen} #{idx}"
                sku = f"SF-{idx:05d}"
                desc = (
                    f"{name} in {cat_name}. Features {descriptions[idx % len(descriptions)]} "
                    f"Tested for high-throughput developer workflows, SKU {sku}."
                )
                price_cents = rng.randrange(low_p, high_p, 100)
                products_to_create.append(
                    Product(
                        sku=sku,
                        name=name,
                        slug=slugify(f"{base_name}-{adj}-{idx}"),
                        description=desc,
                        category=categories[cat_name],
                        price_cents=price_cents,
                        is_active=True,
                    )
                )

            created_products = Product.objects.bulk_create(products_to_create, batch_size=250)
            inventories = [
                Inventory(
                    product=prod,
                    stock_on_hand=rng.randint(5, 120),
                    low_stock_threshold=10,
                )
                for prod in created_products
            ]
            Inventory.objects.bulk_create(inventories, batch_size=250)

            Product.objects.filter(
                pk__in=[p.pk for p in created_products]
            ).update(
                search_vector=(
                    SearchVector("name", weight="A", config="english")
                    + SearchVector("sku", weight="A", config="english")
                    + SearchVector("description", weight="B", config="english")
                )
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(created_products)} products (total={Product.objects.count()}) with GIN search vectors."
            )
        )
