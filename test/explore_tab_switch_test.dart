import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:duanju_app/local_store.dart';
import 'package:duanju_app/models.dart';
import 'package:duanju_app/ui/app_scope.dart';
import 'package:duanju_app/ui/explore/explore_page.dart';

import 'support/fakes.dart';

class GatedRepository extends FakeRepository {
  final gates = <String, Completer<CatalogPage>>{};

  @override
  Future<CatalogPage> catalog(
    String source, {
    int page = 1,
    String query = '',
    String category = '',
    bool force = false,
  }) {
    final gate = gates.putIfAbsent(
      '$source|$category|$query|$page',
      () => Completer<CatalogPage>(),
    );
    return gate.future;
  }
}

Future<void> _pumpExplore(
  WidgetTester tester,
  FakeRepository repository,
  LocalStore store,
) async {
  await tester.pumpWidget(
    MaterialApp(
      home: AppScope(
        repository: repository,
        formFactor: FormFactor.phone,
        child: ExplorePage(store: store),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('fast tab: content switches after tap', (tester) async {
    final repository = FakeRepository();
    final store = await openStore();
    await _pumpExplore(tester, repository, store);
    expect(find.text('推荐剧二'), findsOneWidget);

    await tester.tap(find.text('真人剧'));
    await tester.pumpAndSettle();
    expect(find.text('分类short_play'), findsOneWidget);
    expect(find.text('推荐剧二'), findsNothing);
  });

  testWidgets('slow tab: switches to loading view without dimming', (
    tester,
  ) async {
    final repository = GatedRepository();
    final store = await openStore();
    await _pumpExplore(tester, repository, store);
    expect(find.text('推荐剧二'), findsOneWidget);

    await tester.tap(find.text('真人剧'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump(const Duration(milliseconds: 300));
    // Switched promptly to the new tab's loading state; no 0.5 dim overlay.
    expect(find.text('正在加载'), findsOneWidget);
    expect(find.text('推荐剧二'), findsNothing);

    // Data arrives; content shows.
    repository.gates['hongguo|short_play||1']!
        .complete(CatalogPage([drama('s1', title: '分类剧')]));
    await tester.pumpAndSettle();
    expect(find.text('分类剧'), findsOneWidget);
  });

  testWidgets('hanging tab: loading view shows and stays interactive', (
    tester,
  ) async {
    final repository = GatedRepository();
    final store = await openStore();
    await _pumpExplore(tester, repository, store);

    await tester.tap(find.text('真人剧'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('正在加载'), findsOneWidget);
    expect(
      tester.widgetList<Opacity>(find.byType(Opacity)).map((o) => o.opacity),
      isNot(contains(0.5)),
    );

    // Tabs stay tappable: switch back to the first tab.
    await tester.tap(find.text('推荐'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('推荐剧二'), findsOneWidget);
    expect(find.text('正在加载'), findsNothing);
  });

  testWidgets('rapid taps between tabs settle on the last one', (tester) async {
    final repository = FakeRepository();
    final store = await openStore();
    await _pumpExplore(tester, repository, store);

    await tester.tap(find.text('真人剧'));
    await tester.pump(const Duration(milliseconds: 50));
    await tester.tap(find.text('全部'));
    await tester.pumpAndSettle();
    expect(find.text('分类'), findsOneWidget);
  });
}
