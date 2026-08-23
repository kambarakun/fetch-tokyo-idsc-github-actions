# Prettier Markdown regression fixtures

These samples must remain unchanged after formatting.

## Nested Mermaid example

```markdown
\`\`\`mermaid
stateDiagram-v2
[*] --> Idle
Idle --> Complete: success
Complete --> [*]
\`\`\`
```

## Liquid block after a table

{% macro param_table(params=None) -%}
| Argument | Type |
| -------- | ---- |
{% set default_params = {
    "model": ["str"],
} %}
{% endmacro %}
