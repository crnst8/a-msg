def parse_nonempty(ref):
    stripped = ref.strip()
    return (stripped, None) if stripped else None
