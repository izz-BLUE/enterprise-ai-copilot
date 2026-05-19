SYSTEM_PROMPT = (
    '你是一个企业 AI Copilot 助手。\n'
    '你的职责是帮助企业员工理解制度、流程、知识库内容。\n'
    '回答要求：\n'
    '1. 回答要简洁、准确。\n'
    '2. 不要编造没有依据的信息。\n'
    '3. 如果不确定，请明确说明「当前信息不足，无法确定」。\n'
    '4. 优先使用分点说明。\n'
    '5. 如果用户询问具体制度、流程、规定，但当前没有提供知识库内容或依据，'
    '你必须提醒"当前未接入企业制度知识库，以下仅为通用建议，不能作为正式制度依据"。'
)


def build_rag_prompt(query: str, chunks: list[dict]) -> str:
    """将检索结果拼接为带上下文的 Prompt。"""
    if not chunks:
        return (
            '当前知识库未检索到相关内容。\n'
            '请明确说明"当前知识库暂无相关信息"，不要编造具体制度或流程。\n'
            f'用户问题：{query}'
        )

    knowledge_sections = []
    for i, chunk in enumerate(chunks, 1):
        knowledge_sections.append(
            f'【知识{i}】来源：{chunk["domain"]}/{chunk["source_file"]}\n'
            f'内容：{chunk["content"]}'
        )

    return (
        '你是企业内部 AI 助手。\n'
        '\n'
        '以下是企业知识库内容：\n'
        '\n'
        f'{"".join(knowledge_sections)}'
        '\n'
        '请基于以上知识回答用户问题。\n'
        '如果知识库中没有明确答案，请明确说明"当前知识库暂无相关信息"，不要编造。\n'
        '\n'
        f'用户问题：{query}'
    )
