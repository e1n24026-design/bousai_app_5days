import json
import os
import tempfile
import unittest
from unittest.mock import patch

import app as application


class DisasterFeaturesTestCase(unittest.TestCase):
    def setUp(self):
        self.client = application.app.test_client()
        self.original_shelters = application.shelters[:]
        self.original_instructions = application.instructions[:]
        application.shelters[:] = [
            {
                'id': 1,
                'name': 'Aoba School',
                'district': '青葉',
                'disaster_types': ['洪水', '津波'],
                'pet_friendly': True,
                'barrier_free': True,
                'wheelchair_toilet': False,
                'welfare_space': None,
                'capacity': 100,
                'evacuee_count': 90,
                'latitude': 40.82,
                'longitude': 140.74,
                'address': '青森市青葉1丁目',
                'open_status': '開設中',
            },
            {
                'id': 2,
                'name': 'Aoba Hall',
                'district': '青葉区',
                'disaster_types': ['洪水'],
                'pet_friendly': False,
                'barrier_free': None,
                'wheelchair_toilet': True,
                'welfare_space': True,
                'capacity': 10,
                'evacuee_count': 12,
            },
            {
                'id': 3,
                'name': 'Missing Data Center',
                'capacity': 0,
                'evacuee_count': 0,
                'latitude': 91,
                'longitude': 200,
            },
            {
                'id': 4,
                'name': 'Available Capacity Center',
                'capacity': 100,
                'evacuee_count': 50,
            },
        ]
        application.instructions[:] = []

    def tearDown(self):
        application.shelters[:] = self.original_shelters
        application.instructions[:] = self.original_instructions

    def staff_session(self):
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['username'] = 'staff'
            session['_csrf_token'] = 'test-token'

    def test_shelter_search_filters_all_conditions_and_ignores_unregistered_values(self):
        self.assertEqual(len(application.filter_shelters()), 4)
        self.assertEqual(
            [item['id'] for item in application.filter_shelters(district=' 青葉 ')],
            [1],
        )
        self.assertEqual(
            [item['id'] for item in application.filter_shelters(keyword='aObA')],
            [1, 2],
        )
        self.assertEqual(
            [item['id'] for item in application.filter_shelters(disaster_type='津波')],
            [1],
        )
        self.assertEqual(
            [item['id'] for item in application.filter_shelters(pet_friendly=True)],
            [1],
        )
        self.assertEqual(
            [item['id'] for item in application.filter_shelters(barrier_free=True)],
            [1],
        )
        self.assertEqual(
            [item['id'] for item in application.filter_shelters(
                district='青葉', keyword='school', disaster_type='洪水',
                pet_friendly=True, barrier_free=True,
            )],
            [1],
        )
        self.assertEqual(application.filter_shelters(district='青'), [])
        self.assertEqual(application.filter_shelters(disaster_type='高潮'), [])

    def test_search_page_exposes_only_two_searchable_equipment_conditions(self):
        response = self.client.get('/shelter_search')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="pet_friendly"', response.data)
        self.assertIn(b'name="barrier_free"', response.data)
        self.assertIn(b'name="disaster_type"', response.data)
        self.assertIn(b'name="district"', response.data)
        self.assertIn(b'name="keyword"', response.data)
        self.assertIn('津波'.encode(), response.data)
        self.assertIn('土砂災害'.encode(), response.data)
        self.assertIn('洪水'.encode(), response.data)
        self.assertIn('全施設を見る'.encode(), response.data)
        self.assertNotIn(b'name="wheelchair_toilet"', response.data)
        self.assertNotIn(b'name="welfare_space"', response.data)
        self.assertIn('例：小学校、体育館'.encode(), response.data)

    def test_search_form_criteria_filter_results_with_and_logic(self):
        filtered = self.client.get(
            '/search_results?disaster_type=%E6%B4%AA%E6%B0%B4&district=%E9%9D%92%E8%91%89'
            '&keyword=School&pet_friendly=1&barrier_free=1'
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertIn('1件の避難所'.encode(), filtered.data)
        self.assertIn('Aoba School'.encode(), filtered.data)

        invalid_disaster = self.client.get('/search_results?disaster_type=火山')
        self.assertEqual(invalid_disaster.status_code, 400)

    def test_search_results_and_equipment_statuses_and_occupancy(self):
        response = self.client.get('/search_results')
        self.assertEqual(response.status_code, 200)
        self.assertIn('4件の避難所'.encode(), response.data)
        self.assertIn('住所'.encode(), response.data)
        self.assertIn('情報なし'.encode(), response.data)
        self.assertIn('開設中'.encode(), response.data)
        self.assertIn('100人'.encode(), response.data)
        self.assertIn('90人'.encode(), response.data)
        self.assertIn('ほぼ満員'.encode(), response.data)
        self.assertIn('残り 10人'.encode(), response.data)
        self.assertIn('! 満員'.encode(), response.data)
        self.assertIn('残り 0人'.encode(), response.data)
        self.assertIn('✓ 空きあり'.encode(), response.data)
        self.assertIn(b'occupancy-status nearly-full', response.data)
        self.assertIn(b'occupancy-status full', response.data)
        self.assertIn(b'occupancy-status available', response.data)
        self.assertIn(b'#176b35', response.data)
        self.assertIn(b'#704c00', response.data)
        self.assertIn(b'#8f241a', response.data)
        self.assertIn('? 情報なし'.encode(), response.data)
        self.assertIn('情報なし'.encode(), response.data)
        self.assertIn('ペット同伴可'.encode(), response.data)
        self.assertIn('バリアフリー'.encode(), response.data)
        self.assertIn('車いす対応トイレ'.encode(), response.data)
        self.assertIn('福祉スペース'.encode(), response.data)
        self.assertIn('対応あり'.encode(), response.data)
        self.assertIn('対応なし'.encode(), response.data)

        filtered = self.client.get('/search_results?pet_friendly=1&barrier_free=1')
        self.assertIn('1件の避難所'.encode(), filtered.data)
        self.assertIn('Aoba School'.encode(), filtered.data)
        self.assertNotIn('Aoba Hall'.encode(), filtered.data)
        self.assertIn('ペット同伴可'.encode(), filtered.data)
        self.assertIn('バリアフリー'.encode(), filtered.data)

    def test_empty_search_is_successful_and_has_research_guidance(self):
        application.shelters[:] = [
            {**shelter, 'pet_friendly': False}
            for shelter in application.shelters
        ]
        response = self.client.get('/search_results?pet_friendly=1&barrier_free=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('条件に合う避難所がありません'.encode(), response.data)
        self.assertIn('選択した条件を見直して'.encode(), response.data)
        self.assertIn('href="/shelter_search">検索画面に戻る'.encode(), response.data)

    def test_common_return_navigation_is_fixed_on_multiple_pages(self):
        for path in ('/', '/shelter_search', '/search_results', '/login'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data.count('検索画面に戻る'.encode()), 1)
                self.assertEqual(response.data.count('トップページへ戻る'.encode()), 1)
                self.assertIn(b'position: fixed', response.data)
                self.assertIn(b'env(safe-area-inset-bottom)', response.data)
                self.assertIn(b'padding-bottom: calc(88px', response.data)

    def test_shelter_registration_accepts_optional_comparison_data(self):
        with self.client.session_transaction() as session:
            session['logged_in'] = True
            session['_csrf_token'] = 'test-token'
        with patch.object(application, 'save_shelters') as save:
            response = self.client.post('/shelter_register', data={
                '_csrf_token': 'test-token',
                'name': 'Registered Shelter',
                'address': 'Aomori',
                'open_status': '開設中',
                'capacity': '50',
                'evacuee_count': '12',
                'pet_friendly': 'true',
                'barrier_free': '',
                'wheelchair_toilet': 'false',
                'welfare_space': 'true',
            })
        self.assertEqual(response.status_code, 200)
        save.assert_called_once()
        saved = application.shelters[-1]
        self.assertEqual(saved['capacity'], 50)
        self.assertEqual(saved['evacuee_count'], 12)
        self.assertIs(saved['pet_friendly'], True)
        self.assertIsNone(saved['barrier_free'])
        self.assertIs(saved['wheelchair_toilet'], False)
        self.assertIs(saved['welfare_space'], True)
        application.shelters.pop()

    def test_occupancy_handles_invalid_numbers_and_full_capacity(self):
        self.assertEqual(
            application.shelter_occupancy({'capacity': 0, 'evacuee_count': 4})['status'],
            '情報なし',
        )
        self.assertEqual(
            application.shelter_occupancy({'capacity': 'not a number', 'evacuee_count': 4})['status'],
            '情報なし',
        )
        occupancy = application.shelter_occupancy({'capacity': 5, 'evacuee_count': 8})
        self.assertEqual(occupancy['status'], '満員')
        self.assertEqual(occupancy['remaining'], 0)
        available = application.shelter_occupancy({'capacity': 100, 'evacuee_count': 50})
        self.assertEqual(available['status'], '空きあり')
        self.assertEqual(available['status_class'], 'available')

    def test_weather_parser_filters_city_and_excludes_later_cancellations(self):
        reports = [
            {
                'reportDatetime': '2026-10-18T01:00:00+00:00',
                'warning': {'class20Items': [{
                    'areaCode': application.REGION['area_code'],
                    'kinds': [{'code': '14', 'status': '発表'}],
                }]},
            },
            {
                'reportDatetime': '2026-10-18T02:00:00+00:00',
                'warning': {'class20Items': [
                    {
                        'areaCode': application.REGION['area_code'],
                        'kinds': [
                            {'code': '14', 'status': '解除'},
                            {'code': '05', 'status': '継続'},
                            {'code': '32', 'status': '発表'},
                        ],
                    },
                    {'areaCode': '9999999', 'kinds': [{'code': '06', 'status': '発表'}]},
                ]},
            },
        ]
        warnings, report_time = application.parse_area_warnings(reports)
        self.assertEqual({item['code'] for item in warnings}, {'05', '32'})
        self.assertEqual(
            {item['kind'] for item in warnings},
            {'warning', 'special_warning'},
        )
        self.assertEqual(report_time, '2026-10-18T02:00:00+00:00')

    def test_weather_api_reports_fetch_failure(self):
        with patch.object(application, 'get_weather_warnings', return_value={
            'area_name': application.REGION['name'],
            'warnings': [],
            'error': True,
            'error_message': '取得に失敗しました',
        }):
            response = self.client.get('/api/weather_warnings')
        self.assertEqual(response.status_code, 502)
        self.assertTrue(response.json['error'])

    def test_map_rejects_invalid_coordinates_and_counts_only_valid_markers(self):
        markers = application.map_shelters()
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]['open_status'], '開設中')
        self.assertEqual(application.shelter_open_status({'open_status': '閉鎖'}), '閉鎖中')
        self.assertEqual(application.valid_shelter_coordinates({
            'latitude': True, 'longitude': 0,
        }), None)
        self.assertEqual(application.shelter_open_status({}), '状況未登録')

    def test_home_shows_shelter_empty_state_without_inventing_markers(self):
        application.shelters.clear()
        response = self.client.get('/')
        self.assertIn('登録施設 0件'.encode(), response.data)
        self.assertIn('避難所データはまだ登録されていません'.encode(), response.data)
        self.assertEqual(application.map_shelters(), [])

    def test_home_only_displays_resident_instructions_and_empty_state(self):
        application.instructions[:] = [
            {'id': 1, 'target': '職員', 'content': 'INTERNAL ONLY'},
            {
                'id': 2, 'target': '住民', 'content': '避難してください',
                'urgency': '高', 'status': '未対応', 'district': '市内全域',
                'created_at': '2026-10-18T10:00:00+09:00',
            },
        ]
        response = self.client.get('/')
        self.assertIn('避難してください'.encode(), response.data)
        self.assertNotIn('INTERNAL ONLY'.encode(), response.data)
        self.assertIn('市内全域'.encode(), response.data)
        application.instructions.clear()
        empty = self.client.get('/')
        self.assertIn('現在、住民向けの指示はありません'.encode(), empty.data)

    def test_instruction_create_requires_staff_and_validates_input(self):
        denied = self.client.post('/board/instructions', data={
            'content': 'not authorized',
        })
        self.assertEqual(denied.status_code, 302)
        self.staff_session()
        with patch.object(application, 'save_json') as save:
            invalid = self.client.post('/board/instructions', data={
                '_csrf_token': 'test-token',
                'content': 'x' * 501,
                'target': '住民',
                'district': '市内全域',
                'urgency': '高',
            })
            self.assertEqual(invalid.status_code, 400)
            self.assertIn('500文字以内'.encode(), invalid.data)
            self.assertFalse(save.called)

            invalid_shelter = self.client.post('/board/instructions', data={
                '_csrf_token': 'test-token',
                'content': '避難してください',
                'target': '住民',
                'district': '市内全域',
                'shelter_id': '999',
                'urgency': '高',
            })
            self.assertEqual(invalid_shelter.status_code, 400)
            self.assertFalse(save.called)

            created = self.client.post('/board/instructions', data={
                '_csrf_token': 'test-token',
                'content': '避難してください',
                'target': '住民',
                'district': '青葉',
                'shelter_id': '1',
                'urgency': '高',
            })
            self.assertEqual(created.status_code, 302)
            save.assert_called_once()
        self.assertEqual(application.instructions[0]['status'], '未対応')
        self.assertEqual(application.instructions[0]['shelter_id'], 1)
        self.assertTrue(application.instructions[0]['created_at'].endswith('+09:00'))
        board = self.client.get('/board')
        self.assertIn('指示を登録しました'.encode(), board.data)
        self.assertIn('避難してください'.encode(), board.data)

    def test_instruction_status_update_persists_and_checks_id_and_csrf(self):
        application.instructions[:] = [{
            'id': 23, 'target': '住民', 'content': '対応してください',
            'status': '未対応', 'created_at': '2026-10-18T10:00:00+09:00',
            'updated_at': '2026-10-18T10:00:00+09:00',
        }]
        self.staff_session()
        with patch.object(application, 'save_json') as save:
            no_csrf = self.client.post(
                '/board/instructions/23/status',
                data={'status': '完了'},
            )
            self.assertEqual(no_csrf.status_code, 400)
            self.assertEqual(application.instructions[0]['status'], '未対応')

            invalid_id = self.client.post(
                '/board/instructions/99/status',
                data={'_csrf_token': 'test-token', 'status': '完了'},
            )
            self.assertEqual(invalid_id.status_code, 404)
            self.assertFalse(save.called)

            response = self.client.post(
                '/board/instructions/23/status',
                data={'_csrf_token': 'test-token', 'status': '対応中'},
            )
            self.assertEqual(response.status_code, 302)
            save.assert_called_once()
        self.assertEqual(application.instructions[0]['status'], '対応中')
        self.assertNotEqual(
            application.instructions[0]['updated_at'],
            '2026-10-18T10:00:00+09:00',
        )
        self.assertIn('対応状況を更新しました'.encode(), self.client.get('/board').data)

    def test_instruction_save_failure_is_reported_without_changing_memory(self):
        self.staff_session()
        with patch.object(application, 'save_json', side_effect=OSError('disk unavailable')):
            response = self.client.post('/board/instructions', data={
                '_csrf_token': 'test-token',
                'content': '保存失敗の確認',
                'target': '住民',
                'district': '市内全域',
                'urgency': '中',
            })
        self.assertEqual(response.status_code, 500)
        self.assertIn('入力内容は保存されていません'.encode(), response.data)
        self.assertEqual(application.instructions, [])

    def test_staff_login_accepts_password_123_without_username(self):
        login_page = self.client.get('/login')
        self.assertEqual(login_page.status_code, 200)
        self.assertIn(b'name="password"', login_page.data)
        self.assertNotIn(b'name="username"', login_page.data)
        with self.client.session_transaction() as session:
            token = session['_csrf_token']

        response = self.client.post('/login', data={
            '_csrf_token': token,
            'password': '123',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/board').status_code, 200)

    def test_staff_login_rejects_incorrect_password(self):
        login_page = self.client.get('/login')
        with self.client.session_transaction() as session:
            token = session['_csrf_token']
        response = self.client.post('/login', data={
            '_csrf_token': token,
            'password': 'wrong',
        })
        self.assertEqual(response.status_code, 401)
        self.assertIn('パスワードが正しくありません'.encode(), response.data)

    def test_json_persistence_replaces_complete_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'instructions.json')
            expected = [{'id': 1, 'content': 'persisted'}]
            application.save_json(path, expected)
            with open(path, encoding='utf-8') as source:
                self.assertEqual(json.load(source), expected)
            self.assertEqual(application.load_json(path, []), expected)

    def test_navigation_exposes_routes_and_current_page_accessibly(self):
        response = self.client.get('/shelter_search')
        self.assertIn(b'href="/shelter_search" aria-current="page"', response.data)
        self.assertIn(b'href="/board"', response.data)
        self.assertIn(b'aria-controls="primary-nav"', response.data)
        self.assertIn(b'keydown', response.data)
        self.assertIn(b"event.key === 'Escape'", response.data)


if __name__ == '__main__':
    unittest.main()
