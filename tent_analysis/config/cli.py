"""tent-config: schema, validate, diff, template, migrate."""
import argparse
import sys

from tent_analysis.config import diff as D
from tent_analysis.config import schema as S
from tent_analysis.config import checks as V
from tent_analysis.config import documents, templates
from tent_analysis.config.schema import write_schema


def schema(args) -> int:
    out = write_schema(args.out)
    print(f'wrote {out}')
    return 0


def validate(args) -> int:
    config = documents.load(args.file)
    report = V.validate(config, construct=args.construct or args.steps > 0, base_dir=args.base_dir, steps=args.steps)
    print(report.text())
    return 0 if report.ok else 1


def diff(args) -> int:
    changes = D.diff_configs(documents.load(args.old), documents.load(args.new))
    for change in changes:
        print(change)
    if not changes:
        print('no differences')
        return 0
    verdict = 'every change can be applied in place' if D.in_place(changes) else 'the node must be rebuilt'
    print(f'{len(changes)} change{"s" if len(changes) != 1 else ""}: {verdict}')
    return 0 if D.in_place(changes) else 2


def template(args) -> int:
    if args.list_classes:
        for slot, names in templates.slots().items():
            print(f'{slot}: {", ".join(names)}')
        return 0
    if args.slot == 'TransactiveNode':
        entry = templates.node_template(doc=not args.no_doc)
    else:
        if not args.class_name:
            print('class_name is required for a component template (see --list-classes)', file=sys.stderr)
            return 2
        entry = templates.template(args.slot, args.class_name, args.market_class_name, doc=not args.no_doc)
    text = documents.dump(entry, args.out)
    if args.out:
        print(f'wrote {args.out}')
    else:
        sys.stdout.write(text)
    return 0


def new(args) -> int:
    config = templates.new(args.template, doc=not args.no_doc)
    text = documents.dump(config, args.out)
    if args.out:
        print(f'wrote {args.out}; fill the placeholders validate lists')
    else:
        sys.stdout.write(text)
    return 0


def add(args) -> int:
    config = documents.load(args.file)
    try:
        updated = templates.add_entry(config, args.list, args.class_name, args.market_class_name, args.name, doc=args.doc)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    report = V.validate_structure(updated).extend(V.validate_semantics(updated))
    out = args.out or args.file
    documents.dump(updated, out)
    print(f'wrote {out}')
    if not report.ok:
        print('the file now has problems to resolve (the new entry\'s required fields, usually):', file=sys.stderr)
        print(report.text(), file=sys.stderr)
        return 1
    return 0


def docs(args) -> int:
    from tent_analysis.config.docs import write_docs
    path = write_docs(args.out, args.format)
    print(f'wrote {path}')
    return 0


def migrate(args) -> int:
    migrated, notes = documents.migrate(documents.load(args.file))
    for note in notes:
        print(f'migrated {note}')
    if not notes:
        print('nothing to migrate')
    report = V.validate_semantics(migrated)
    if args.out:
        documents.dump(migrated, args.out)
        print(f'wrote {args.out}')
    if not report.ok:
        print('the models still reject the migrated configuration:', file=sys.stderr)
        print(report.text(), file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='tent-config', description='Produce, validate, compare and migrate TENT node '
                                                                    'configurations with TENT\'s configuration models.')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('schema', help="write TENT's JSON Schema of a node configuration")
    p.add_argument('--out', default='tent-node.schema.json')
    p.set_defaults(func=schema)
    p = sub.add_parser('validate', help='check a configuration file: structure (schema), semantics (models), and with '
                                        '--construct a node build with a NullDataManager')
    p.add_argument('file')
    p.add_argument('--construct', action='store_true')
    p.add_argument('--base-dir', help='directory relative paths in the file resolve against for --construct '
                                      '(default: the working directory, as for a node)')
    p.add_argument('--steps', type=int, default=0, help='after building, run this many state-machine iterations along '
                                                        "the first market's transition times (implies --construct)")
    p.set_defaults(func=validate)
    p = sub.add_parser('new', help='a whole node configuration from a template: the TENT sample campus, or the '
                                   'deployed city and small offices, with deployment paths left as placeholders')
    p.add_argument('--template', required=True, choices=templates.template_names())
    p.add_argument('--no-doc', action='store_true', help=f'omit the {documents.DOC_KEY!r} descriptions')
    p.add_argument('--out')
    p.set_defaults(func=new)
    p = sub.add_parser('add', help='append a template entry for a registered class to a list of an existing file')
    p.add_argument('file')
    p.add_argument('--list', required=True, choices=sorted(S.SLOTS), help='the node list (or data_manager, replaced)')
    p.add_argument('--class', dest='class_name', required=True)
    p.add_argument('--market-class-name', dest='market_class_name')
    p.add_argument('--name', help='name of the new entry (default: the class name); must be new in the list')
    p.add_argument('--doc', action='store_true', help=f'include the {documents.DOC_KEY!r} descriptions in the entry')
    p.add_argument('--out', help='write here instead of back into FILE')
    p.set_defaults(func=add)
    p = sub.add_parser('docs', help='write the "Configuring a node" page generated from the schema')
    p.add_argument('--out', required=True, help='directory to write into')
    p.add_argument('--format', choices=['md', 'rst'], default='md')
    p.set_defaults(func=docs)
    p = sub.add_parser('diff', help='compare two configurations; exit 0 when every change is reconfigurable in place, '
                                    '2 when the node must be rebuilt')
    p.add_argument('old')
    p.add_argument('new')
    p.set_defaults(func=diff)
    p = sub.add_parser('template', help='a new entry for a registered class, every field at its default with its '
                                        f'description under {documents.DOC_KEY!r}')
    p.add_argument('slot', nargs='?', default='TransactiveNode',
                   help='TransactiveNode (whole file) or a slot: MeterPoint, InformationServiceModel, LocalAsset, '
                        'Neighbor, MarketSeries, DataManager, PriceModel, Model, ActuationManager, OccupancyManager')
    p.add_argument('class_name', nargs='?')
    p.add_argument('--market-class-name', dest='market_class_name', help='for a MarketSeries: Auction, ConsensusMarket, '
                                                                        'MatchingEngine or Market')
    p.add_argument('--no-doc', action='store_true', help=f'omit the {documents.DOC_KEY!r} descriptions')
    p.add_argument('--list-classes', action='store_true', help='list the registered classes per slot and exit')
    p.add_argument('--out')
    p.set_defaults(func=template)
    p = sub.add_parser('migrate', help='rewrite legacy spellings (camelCase lists, model_type, db_type, location "") '
                                       'and report what the models still reject')
    p.add_argument('file')
    p.add_argument('--out', help='write the migrated configuration here (default: report only)')
    p.set_defaults(func=migrate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
