import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:http/http.dart' as http;

import 'dart:convert';

/// 课程归档浏览：课程 → 会话 → 笔记/总结。
class ArchivePage extends StatefulWidget {
  const ArchivePage({super.key, required this.baseUrl, required this.token});

  final String baseUrl;
  final String token;

  @override
  State<ArchivePage> createState() => _ArchivePageState();
}

class _ArchivePageState extends State<ArchivePage> {
  List<Map<String, dynamic>>? _courses;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Uri _uri(String path) => Uri.parse('${widget.baseUrl}$path').replace(
        queryParameters: {'token': widget.token},
      );

  Future<void> _load() async {
    try {
      final resp = await http.get(_uri('/api/archive'));
      if (resp.statusCode != 200) throw Exception('HTTP ${resp.statusCode}');
      final data = (jsonDecode(resp.body) as List).cast<Map<String, dynamic>>();
      setState(() => _courses = data);
    } catch (e) {
      setState(() => _error = '加载失败: $e');
    }
  }

  Future<String> _loadFile(String course, String date, String session, String file) async {
    final resp = await http.get(_uri('/api/archive/$course/$date/$session/$file'));
    if (resp.statusCode != 200) throw Exception('HTTP ${resp.statusCode}');
    return (jsonDecode(resp.body) as Map<String, dynamic>)['content'] as String;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('课程归档')),
      body: _error != null
          ? Center(child: Text(_error!))
          : _courses == null
              ? const Center(child: CircularProgressIndicator())
              : _courses!.isEmpty
                  ? const Center(child: Text('还没有归档，去上一节课吧'))
                  : ListView.builder(
                      itemCount: _courses!.length,
                      itemBuilder: (_, i) {
                        final course = _courses![i];
                        return ExpansionTile(
                          title: Text(course['course'] as String),
                          subtitle: Text('${(course['sessions'] as List).length} 次课'),
                          children: [
                            for (final sess in course['sessions'] as List)
                              for (final file in (sess['files'] as List).cast<String>())
                                if (file.endsWith('.md'))
                                  ListTile(
                                    dense: true,
                                    leading: Icon(
                                      file == 'session_summary.md'
                                          ? Icons.summarize
                                          : Icons.sticky_note_2,
                                      size: 18,
                                    ),
                                    title: Text(
                                        '${sess['date']} ${sess['session']} ${file == 'session_summary.md' ? '· 复习总结' : '· 课堂笔记'}'),
                                    onTap: () => _openFile(
                                      course['course'] as String,
                                      sess['date'] as String,
                                      sess['session'] as String,
                                      file,
                                    ),
                                  ),
                          ],
                        );
                      },
                    ),
    );
  }

  Future<void> _openFile(String course, String date, String session, String file) async {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => FutureBuilder<String>(
        future: _loadFile(course, date, session, file),
        builder: (context, snap) {
          if (snap.hasError) {
            return Scaffold(appBar: AppBar(), body: Center(child: Text('${snap.error}')));
          }
          if (!snap.hasData) {
            return const Scaffold(body: Center(child: CircularProgressIndicator()));
          }
          return Scaffold(
            appBar: AppBar(title: Text('$course · $date')),
            body: Markdown(data: snap.data!, selectable: true),
          );
        },
      ),
    ));
  }
}
