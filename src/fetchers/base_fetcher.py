"""
東京都感染症発生動向情報システムからデータを取得する基底クラス
"""

import hashlib
import time
from datetime import datetime, date
from pathlib import Path
from typing import Literal

import requests


class TokyoEpidemicSurveillanceFetcher:
    """
    東京都感染症発生動向情報 データダウンロードからデータを取得するクラス

    対象: 感染症発生動向情報 データダウンロード
    URL: https://survey.tmiph.metro.tokyo.lg.jp/epidinfo/epimenu.do

    都道府県コード:
        '13': 東京都

    医療圏コード:
        '00': 全て

    週次感染症コード:
        '00':     全て
        '501':    インフルエンザ
        '2032':   新型コロナウイルス感染症（COVID-19）
        '300000': 急性呼吸器感染症（ARI）
        '10017':  RSウイルス感染症
        '601':    咽頭結膜熱
        '602':    Ａ群溶血性レンサ球菌咽頭炎
        '603':    感染性胃腸炎
        '604':    水痘
        '605':    手足口病
        '606':    伝染性紅斑
        '607':    突発性発しん
        '610':    ヘルパンギーナ
        '612':    流行性耳下腺炎
        '10001':  不明発しん症
        '10002':  川崎病
        '701':    急性出血性結膜炎
        '702':    流行性角結膜炎
        '902':    細菌性髄膜炎
        '903':    無菌性髄膜炎
        '904':    マイコプラズマ肺炎
        '905':    クラミジア肺炎(オウム病は除く)
        '2030':   感染性胃腸炎（ロタウイルス）
        '200000': COVID-19入院
        '100000': インフルエンザ入院

    月次感染症コード:
        '00':    全て
        '801':   性器クラミジア感染症
        '802':   性器ヘルペスウイルス感染症
        '803':   尖圭コンジローマ
        '804':   淋菌感染症
        '10003': 膣トリコモナス症
        '10004': 梅毒様疾患
        '951':   メチシリン耐性黄色ブドウ球菌感染症
        '952':   ペニシリン耐性肺炎球菌感染症
        '953':   薬剤耐性緑膿菌感染症

    集計モード:
        '0': 集計期間内の合計
        '1': 集計期間内の週毎内訳（epidCodeを指定しないと動作しない）
    """

    BASE_URL = 'https://survey.tmiph.metro.tokyo.lg.jp/epidinfo'

    # reportTypeとURLのマッピング
    ENDPOINT_MAP = {
        # 週報告分
        '0':  'dlwage.do',     # 年齢階級別集計表
        '1':  'dlwgender.do',  # 男女別集計表
        '2':  'dlwhc.do',      # 保健所別集計表
        '5':  'dlwzone.do',    # 医療圏別集計表
        # 月報告分
        '10': 'dlmage.do',     # 月次年齢階級別集計表
        '15': 'dlmgender.do',  # 月次男女別集計表
        '11': 'dlmhc.do',      # 月次保健所別集計表
        '12': 'dlmzone.do',    # 月次医療圏別集計表
        # 全数報告
        '20': 'dlwzensu.do',   # 全数報告疾病
    }

    def __init__(self):
        self.session = requests.Session()

    def _post_request(
        self,
        endpoint:         str,
        report_type:      str,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '00',
        total_mode:       str = '0'
    ) -> bytes:
        """
        共通のPOSTリクエスト処理

        Args:
            endpoint: APIエンドポイント
            report_type: レポートタイプ
            start_year: 開始年
            start_sub_period: 開始週/月
            end_year: 終了年
            end_sub_period: 終了週/月
            pref_code: 都道府県コード (13=東京都)
            hc_code: 医療圏コード (00=全て)
            epid_code: 感染症コード (週次・月次感染症コード参照)
            total_mode: 集計モード (0=合計, 1=週毎内訳)

        Returns:
            bytes: CSVデータ（Shift_JISエンコード）
        """
        url = f"{self.BASE_URL}/{endpoint}"

        data = {
            'val(reportType)': report_type,
            'val(prefCode)': pref_code,
            'val(hcCode)': hc_code,
            'val(epidCode)': epid_code,
            'val(startYear)': start_year,
            'val(startSubPeriod)': start_sub_period,
            'val(endYear)': end_year,
            'val(endSubPeriod)': end_sub_period,
            'val(totalMode)': total_mode
        }

        response = self.session.post(url, data=data)

        if response.status_code == 200:
            return response.content
        else:
            raise Exception(f"Request failed with status code: {response.status_code}")

    # ========== 定点監視 週報告分データ取得メソッド ==========

    def fetch_csv_sentinel_weekly_gender(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '00',
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 週報告分 男女別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['1'], '1',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    def fetch_csv_sentinel_weekly_age(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '00',
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 週報告分 年齢階級別集計表CSVを取得する（男女別を含む）
        """
        return self._post_request(
            self.ENDPOINT_MAP['0'], '0',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    def fetch_csv_sentinel_weekly_health_center(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '',  # 保健所別は通常空文字
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 週報告分 保健所別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['2'], '2',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    def fetch_csv_sentinel_weekly_medical_district(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '',  # 医療圏別は通常空文字
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 週報告分 医療圏別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['5'], '5',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    # ========== 定点監視 月報告分データ取得メソッド ==========

    def fetch_csv_sentinel_monthly_gender(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',  # 月次は月を指定
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '00',
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 月報告分 男女別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['15'], '15',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    def fetch_csv_sentinel_monthly_age(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '00',
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 月報告分 年齢階級別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['10'], '10',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    def fetch_csv_sentinel_monthly_health_center(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '',  # 月次保健所別は通常空文字
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 月報告分 保健所別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['11'], '11',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    def fetch_csv_sentinel_monthly_medical_district(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '',  # 月次医療圏別は通常空文字
        total_mode:       str = '0'
    ) -> bytes:
        """
        定点監視 月報告分 医療圏別集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['12'], '12',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )

    # ========== 全数把握監視データ取得メソッド ==========

    def fetch_csv_notifiable_weekly(
        self,
        start_year:       str = '2025',
        start_sub_period: str = '1',  # 週を指定
        end_year:         str = '2025',
        end_sub_period:   str = '1',
        pref_code:        str = '13',
        hc_code:          str = '00',
        epid_code:        str = '',  # 全数報告は通常空文字
        total_mode:       str = '0'
    ) -> bytes:
        """
        全数把握監視 週報告分 届出患者数集計表CSVを取得する
        """
        return self._post_request(
            self.ENDPOINT_MAP['20'], '20',
            start_year, start_sub_period, end_year, end_sub_period,
            pref_code, hc_code, epid_code, total_mode
        )