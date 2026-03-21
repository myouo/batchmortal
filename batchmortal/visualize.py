import os
import csv
import json
import logging
from datetime import datetime
import openpyxl

def read_results(nickname: str, output_format: str = "xlsx") -> list[dict]:
    safe_nick = "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    filepath = os.path.join(base_dir, "results", safe_nick, f"results.{output_format}")
    
    if not os.path.exists(filepath):
        logging.warning(f"No results found for {nickname} at {filepath}")
        return []
        
    records = []
    if output_format == "csv":
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
    elif output_format == "xlsx":
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
        rows = list(ws.rows)
        if len(rows) > 1:
            headers = [str(cell.value) if cell.value else "" for cell in rows[0]]
            for row in rows[1:]:
                record = {}
                for idx, cell in enumerate(row):
                    if idx < len(headers):
                        record[headers[idx]] = cell.value if cell.value is not None else ""
                records.append(record)
        wb.close()
        
    def parse_time(ts_str):
        ts_str = str(ts_str).strip()
        if not ts_str:
            return 0.0
        try:
            if ts_str.endswith('Z'):
                return datetime.fromisoformat(ts_str[:-1]).timestamp()
            return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            try:
                # fallback for other datetime strings if any
                return datetime.fromisoformat(ts_str).timestamp()
            except Exception:
                return 0.0
            
    records.sort(key=lambda r: parse_time(r.get("startTime") or r.get("timestamp") or ""))
    return records


def calculate_regression(y_vals: list[float]) -> list[float]:
    import numpy as np
    n = len(y_vals)
    if n <= 1:
        return y_vals
    
    x = np.arange(n)
    y = np.array(y_vals)
    
    # 动态选用多项式拟合阶数：如果数据量不足以支撑3阶（即曲线），则降级为低阶。
    # 3阶多项式能完美刻画玩家处于“上升->瓶颈->回落/突破”的非线性波动周期。
    deg = 3 if n >= 4 else (2 if n == 3 else 1)
    
    try:
        coeffs = np.polyfit(x, y, deg)
        poly = np.poly1d(coeffs)
        return [float(val) for val in poly(x)]
    except Exception:
        # Fallback to horizontal mean if svd fails (extremely rare edge case)
        return [sum(y_vals) / n] * n


def generate_html(nickname: str, output_path: str, format_type: str = "xlsx") -> str | None:
    records = read_results(nickname, format_type)
    if not records:
        return None
        
    full_times = []
    times = []
    ratings = []
    ai_rates = []
    
    for r in records:
        t = str(r.get("startTime") or r.get("timestamp") or "").split(" ")[0]
        rating_str = str(r.get("rating", ""))
        ai_str = str(r.get("aiConsistencyRate", ""))
        
        try:
            rating = float(rating_str)
        except ValueError:
            continue
            
        try:
            ai_rate = float(ai_str.replace("%", "").strip())
        except ValueError:
            ai_rate = 0.0
            
        full_time_label = str(r.get("startTime") or r.get("timestamp") or "")
        split_t = full_time_label.split(" ")
        time_label = full_time_label
        if len(split_t) == 2:
            time_label = split_t[0].split("-", 1)[-1] + " " + split_t[1][:5] # Kept as space here, formatter will do \n
            
        full_times.append(full_time_label)
        times.append(time_label)
        ratings.append(rating)
        ai_rates.append(ai_rate)
        
    if not ratings:
        logging.warning("No valid rating data found to plot.")
        return None
        
    regression_line = calculate_regression(ratings)
    
    html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{nickname} Mortal Analysis</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background-color: #f8f9fa;
        }}
        #main {{
            width: 1300px;
            height: 750px;
            margin: 40px auto;
            background-color: #ffffff;
            border-radius: 8px;
            padding: 20px;
        }}
    </style>
</head>
<body>
    <div id="main"></div>
    <script>
        var chartDom = document.getElementById('main');
        var myChart = echarts.init(chartDom);
        
        const fullTimeData = {json.dumps(full_times)};
        const timeData = {json.dumps(times)};
        const ratingData = {json.dumps(ratings)};
        const aiData = {json.dumps(ai_rates)};
        const regressionData = {json.dumps(regression_line)};
        
        var option = {{
            textStyle: {{
                fontFamily: '"STZhongsong", "华文中宋", "Microsoft YaHei", "PingFang SC", sans-serif'
            }},
            animation: false,
            title: {{
                text: '{nickname} 的Mortal解析统计',
                left: 'center',
                top: 10,
                textStyle: {{
                    fontSize: 26,
                    fontWeight: 'bold',
                    color: '#1f2937'
                }}
            }},
            tooltip: {{
                trigger: 'axis',
                backgroundColor: 'rgba(255, 255, 255, 0.95)',
                formatter: function (params) {{
                    let res = `<div style="font-weight:bold;margin-bottom:5px;font-size:14px;color:#374151;">${{fullTimeData[params[0].dataIndex]}}</div>`;
                    params.forEach(function (item) {{
                        if (item.componentType === 'markLine') return;
                        let val = item.value;
                        if (typeof val === 'number') {{
                            val = val.toFixed(2);
                        }}
                        res += `
                            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:5px;">
                                <span>${{item.marker}} <span style="color:#4b5563;">${{item.seriesName}}</span></span>
                                <span style="font-size:16px;font-weight:bold;margin-left:20px;color:#111827;">${{val}}</span>
                            </div>`;
                    }});
                    return res;
                }}
            }},
            legend: {{
                data: ['Rating', 'AI一致率', '趋势预测线 (Rating)'],
                top: 55,
                textStyle: {{ fontSize: 15, color: '#4b5563' }}
            }},
            grid: {{
                left: '5%',
                right: '5%',
                bottom: '15%',
                top: '20%',
                containLabel: true
            }},
            dataZoom: [
                {{
                    type: 'inside',
                    start: 0,
                    end: 100
                }},
                {{
                    type: 'slider',
                    start: 0,
                    end: 100,
                    bottom: '2%',
                    borderColor: 'transparent',
                    fillerColor: 'rgba(99, 102, 241, 0.1)',
                    handleStyle: {{ color: '#4f46e5' }}
                }}
            ],
            visualMap: {{
                show: false,
                type: 'continuous',
                seriesIndex: 0,
                min: 80,
                max: 100,
                inRange: {{
                    color: ['#ef4444', '#10b981', '#4f46e5', '#fbbf24']
                }}
            }},
            xAxis: [
                {{
                    type: 'category',
                    data: timeData,
                    axisLabel: {{ 
                        rotate: 30, 
                        fontSize: 13, 
                        color: '#6b7280',
                        formatter: function(value) {{
                            return value.replace(' ', '\\n');
                        }}
                    }},
                    axisTick: {{ show: false }},
                    splitLine: {{ show: false }}
                }}
            ],
            yAxis: [
                {{
                    type: 'value',
                    name: 'Rating',
                    min: 80,
                    max: 100,
                    axisLabel: {{ formatter: '{{value}}', fontSize: 14, color: '#6b7280' }},
                    nameTextStyle: {{ fontSize: 15, color: '#1f2937', fontWeight: 'bold' }},
                    splitLine: {{
                        show: true,
                        lineStyle: {{ type: 'dashed', color: '#e5e7eb' }}
                    }}
                }},
                {{
                    type: 'value',
                    name: 'AI一致率',
                    min: 0,
                    max: 100,
                    axisLabel: {{ formatter: '{{value}} %', fontSize: 14, color: '#6b7280' }},
                    nameTextStyle: {{ fontSize: 15, color: '#1f2937', fontWeight: 'bold' }},
                    splitLine: {{ show: false }}
                }}
            ],
            series: [
                {{
                    name: 'Rating',
                    type: 'line',
                    smooth: 0.4,
                    data: ratingData,
                    symbolSize: 12,
                    itemStyle: {{
                        borderWidth: 4,
                        borderColor: '#ffffff'
                    }},
                    lineStyle: {{
                        width: 6,
                        shadowColor: 'rgba(0,0,0,0.15)',
                        shadowBlur: 20,
                        shadowOffsetY: 10
                    }}
                }},
                {{
                    name: '趋势预测线 (Rating)',
                    type: 'line',
                    data: regressionData,
                    symbol: 'none',
                    lineStyle: {{
                        type: 'dashed',
                        width: 2,
                        color: '#fbbf24'
                    }}
                }},
                {{
                    name: 'AI一致率',
                    type: 'bar',
                    yAxisIndex: 1,
                    data: aiData,
                    barMaxWidth: 30,
                    itemStyle: {{
                        borderRadius: [4, 4, 0, 0],
                        color: {{
                            type: 'linear',
                            x: 0, y: 0, x2: 0, y2: 1,
                            colorStops: [
                                {{ offset: 0, color: 'rgba(99, 102, 241, 0.2)' }},
                                {{ offset: 1, color: 'rgba(99, 102, 241, 0.05)' }}
                            ]
                        }}
                    }}
                }}
            ]
        }};
        
        myChart.setOption(option);
    </script>
</body>
</html>
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_template)
    return output_path

def save_png(html_path: str, png_path: str):
    from seleniumbase import SB
    import urllib.parse
    
    abs_html = os.path.abspath(html_path)
    file_url = "file:///" + urllib.parse.quote(abs_html.replace("\\", "/"))
    
    with SB(uc=True, headless=True) as sb:
        sb.open(file_url)
        sb.sleep(1.0)
        sb.save_screenshot(png_path, selector="#main")

def plot_results(nickname: str, plot_mode: str, output_format: str = "xlsx"):
    if plot_mode in ["none", None]:
        return
        
    safe_nick = "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_root = os.path.join(base_dir, "results", safe_nick)
    os.makedirs(output_root, exist_ok=True)
    
    html_path = os.path.join(output_root, f"report_{safe_nick}.html")
    png_path = os.path.join(output_root, f"report_{safe_nick}.png")
    
    logging.info(f"Generating charts for {nickname} (Mode: {plot_mode})...")
    res = generate_html(nickname, html_path, output_format)
    if not res:
        logging.warning("Skipping chart generation.")
        return
        
    if plot_mode in ["png", "both"]:
        try:
            save_png(html_path, png_path)
            logging.info(f"Saved PNG chart to: {png_path}")
        except Exception as e:
            logging.error(f"Failed to render PNG chart: {e}")
            
    if plot_mode in ["html", "both"]:
        logging.info(f"Saved HTML chart to: {html_path}")
    elif plot_mode == "png":
        try:
            os.remove(html_path)
        except OSError:
            pass

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fmt = "xlsx"
        plot_results(sys.argv[1], "both", fmt)
