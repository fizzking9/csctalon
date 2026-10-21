SELECT '== ods.cs_records columns ==' AS info;
SELECT ordinal_position, column_name, data_type
FROM information_schema.columns
WHERE table_schema='ods' AND table_name='cs_records'
ORDER BY ordinal_position;
SELECT '== ods.bms_orders columns ==' AS info;
SELECT ordinal_position, column_name, data_type
FROM information_schema.columns
WHERE table_schema='ods' AND table_name='bms_orders'
ORDER BY ordinal_position;
