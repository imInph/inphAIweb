"""Tools a model may call: built in only (no user-written code runs on the server)."""
import ast
import datetime
import math
import operator
import jobs
import knowledge
import settings
import store
import web

RESULT_CHARS = 12_000
SPECS = [
    {'name': 'web_search', 'description': 'Search the web and read the best matching passages from the top pages.',
     'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'What to search for'}},
                    'required': ['query']}},
    {'name': 'open_url', 'description': 'Read the text of one web page.',
     'parameters': {'type': 'object', 'properties': {'url': {'type': 'string', 'description': 'The http(s) address'}},
                    'required': ['url']}},
    {'name': 'search_knowledge', 'description': "Search the user's own documents (their knowledge collections).",
     'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': 'What to look for'}},
                    'required': ['query']}},
    {'name': 'calculator', 'description': 'Work out an arithmetic expression exactly, e.g. "sqrt(2) * (3 + 4) ** 2".',
     'parameters': {'type': 'object', 'properties': {'expression': {'type': 'string'}}, 'required': ['expression']}},
    {'name': 'current_datetime', 'description': "The date, time and weekday where the user's server is.",
     'parameters': {'type': 'object', 'properties': {}}},
    {'name': 'generate_image', 'description': 'Draw a picture. It is queued on the GPU and appears in the chat when done.',
     'parameters': {'type': 'object', 'properties': {
         'prompt': {'type': 'string', 'description': 'A detailed description of the picture'},
         'style': {'type': 'string', 'enum': ['anime', 'realistic'], 'description': 'anime (WAI) or realistic (Z-Image)'}},
         'required': ['prompt']}},
]


def openai_tools():
    return [{'type': 'function', 'function': spec} for spec in SPECS]


# A calculator that only does arithmetic: the expression is parsed, never eval'd.
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
             ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
             ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCTIONS = {name: getattr(math, name) for name in ('sqrt', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'log', 'log10',
                                                     'log2', 'exp', 'floor', 'ceil', 'factorial', 'radians', 'degrees')}
FUNCTIONS.update(abs=abs, round=round, min=min, max=max)
CONSTANTS = {'pi': math.pi, 'e': math.e, 'tau': math.tau}


def calculate(expression):
    def value(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name) and node.id in CONSTANTS:
            return CONSTANTS[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in OPERATORS:
            return OPERATORS[type(node.op)](value(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 1000:
                raise ValueError('That power is too large')
            return OPERATORS[type(node.op)](left, right)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
                and not node.keywords):
            args = [value(arg) for arg in node.args]
            if node.func.id == 'factorial' and args and args[0] > 1000:
                raise ValueError('That factorial is too large')
            return FUNCTIONS[node.func.id](*args)
        raise ValueError('Only numbers, + - * / // % **, brackets and maths functions are allowed')
    if len(expression) > 500:
        raise ValueError('That expression is too long')
    return value(ast.parse(expression.replace('^', '**'), mode='eval').body)


def run(name, arguments, context):
    """Run one tool call. context: user, chat, sources (a list the web/knowledge tools add to).
    Returns (text for the model, a short summary for the page)."""
    user, chat = context['user'], context['chat']
    try:
        if name == 'calculator':
            result = calculate(str(arguments.get('expression', '')))
            return str(result), f'= {result}'
        if name == 'current_datetime':
            now = datetime.datetime.now().astimezone()
            return now.strftime('%A %d %B %Y, %H:%M (%Z, UTC%z)'), now.strftime('%a %d %b %Y %H:%M')
        if name == 'web_search':
            query = str(arguments.get('query', '')).strip()
            found = web.research(query, query, user['role'] == 'admin', lambda status: None)
            return cite(found, context), f'{len(found)} passages from the web'
        if name == 'open_url':
            url = str(arguments.get('url', '')).strip()
            title, text = web.fetch_page(url, allow_private=user['role'] == 'admin')
            return f'{title}\n\n{text[:RESULT_CHARS]}', title
        if name == 'search_knowledge':
            query = str(arguments.get('query', '')).strip()
            ids = store.owned_knowledge(chat['knowledge_ids'], user['id']) or [k['id'] for k in store.list_knowledge(user['id'])]
            if not ids:
                return 'The user has no knowledge collections.', 'no collections'
            passages = knowledge.retrieve(ids, query)
            found = [{'kind': 'knowledge', 'document': p['document'], 'document_id': p['document_id'],
                      'knowledge': p['knowledge'], 'knowledge_id': p['knowledge_id'], 'text': p['text']} for p in passages]
            return cite(found, context), f'{len(found)} passages from your documents'
        if name == 'generate_image':
            prompt = str(arguments.get('prompt', '')).strip()
            style = arguments.get('style') if arguments.get('style') in ('anime', 'realistic') else 'anime'
            params = jobs.validate_image({'prompt': prompt, 'model': style,
                                          **({'negative_prompt': settings.get('image_negative_prompt')} if style == 'anime' else {})})
            job_id = jobs.enqueue(user['id'], 'image', params, chat_id=chat['id'], message_id=context['message_id'])
            context['jobs'].append(job_id)
            return (f'The image is queued (job {job_id}). It will appear in this chat when it is done; '
                    'tell the user it is on its way.'), f'queued image job {job_id}'
        return f'There is no tool called {name}.', 'unknown tool'
    except Exception as error:
        return f'The tool failed: {error}', f'failed: {error}'


def cite(found, context):
    """Number passages after the sources already in this answer, and add them to its Sources list."""
    sources = context['sources']
    lines = []
    for item in found:
        n = len(sources) + 1
        sources.append({**item, 'n': n})
        title = item.get('title') or item.get('document')
        lines.append(f'[{n}] {title}{" (" + item["url"] + ")" if item.get("url") else ""}\n{item["text"]}')
    if not lines:
        return 'Nothing found.'
    return 'Cite these by their number in brackets.\n\n' + '\n\n'.join(lines)
