import json
from pathlib import Path


def extract_secu_codes(input_folder: Path, output_file: Path):
    """
    从指定文件夹中的所有 JSON 文件提取 SECUCODE，并生成新的 JSON 文件。

    Args:
        input_folder (str): 包含 JSON 文件的文件夹路径。
        output_file (str): 生成的 JSON 文件路径。
    """
    result = {}

    # 遍历文件夹中的所有 JSON 文件
    for json_file in input_folder.glob("*.json"):
        try:
            # 读取 JSON 文件内容
            with json_file.open('r', encoding='utf-8') as f:
                content = json.load(f)

            # 从数据中提取 SECUCODE
            secucodes = [
                item.get('SECURITY_CODE')
                for item in content.get('result', {}).get('data', [])
                if 'SECURITY_CODE' in item
            ]

            # 使用文件名（不带扩展名）作为 key
            result[json_file.stem] = secucodes
        except Exception as e:
            print(f"Error processing {json_file.name}: {e}")

    # 将结果写入新的 JSON 文件
    with output_file.open('w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)

    print(f"Data has been extracted and saved to {output_file}")


extract_secu_codes(
    Path(__file__).parent / 'em_data',
    Path(__file__).parents[1] / 'utils' / 'output.json',
)
