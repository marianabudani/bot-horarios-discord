import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import json
import re
from collections import defaultdict

# ============ CONFIGURACIÓN ============
# IDs de los canales
CANAL_SERVICIOAPP = 1448835558410289183  # Canal donde ServicioAPP publica
CANAL_COMANDOS = 1448858691670376468      # Canal donde el bot responde

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Almacenamiento de datos
class ShiftTracker:
    def __init__(self):
        self.active_shifts = {}  # {dni: {'nombre': str, 'entrada': datetime}}
        self.daily_records = defaultdict(lambda: defaultdict(list))  # {fecha: {dni: [turnos]}}
        self.weekly_stats = defaultdict(lambda: {
            'total_horas': 0,
            'total_entradas': 0,
            'nombre': '',
            'daily_hours': defaultdict(float),
            'daily_entries': defaultdict(int)
        })
    
    def save_data(self):
        """Guarda los datos en un archivo JSON"""
        data = {
            'active_shifts': {
                dni: {
                    'nombre': info['nombre'],
                    'entrada': info['entrada'].isoformat()
                } for dni, info in self.active_shifts.items()
            },
            'daily_records': {
                fecha: {
                    dni: [
                        {
                            'entrada': turno['entrada'].isoformat(),
                            'salida': turno['salida'].isoformat() if turno['salida'] else None,
                            'horas': turno['horas']
                        } for turno in turnos
                    ] for dni, turnos in records.items()
                } for fecha, records in self.daily_records.items()
            },
            'weekly_stats': dict(self.weekly_stats)
        }
        with open('shift_data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def load_data(self):
        """Carga los datos desde el archivo JSON"""
        try:
            with open('shift_data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Restaurar turnos activos
            for dni, info in data.get('active_shifts', {}).items():
                self.active_shifts[dni] = {
                    'nombre': info['nombre'],
                    'entrada': datetime.fromisoformat(info['entrada'])
                }
            
            # Restaurar registros diarios
            for fecha, records in data.get('daily_records', {}).items():
                for dni, turnos in records.items():
                    self.daily_records[fecha][dni] = [
                        {
                            'entrada': datetime.fromisoformat(turno['entrada']),
                            'salida': datetime.fromisoformat(turno['salida']) if turno['salida'] else None,
                            'horas': turno['horas'],
                            'nombre': turno.get('nombre', '')
                        } for turno in turnos
                    ]
            
            # Restaurar estadísticas semanales
            self.weekly_stats = defaultdict(lambda: {
                'total_horas': 0,
                'total_entradas': 0,
                'nombre': '',
                'daily_hours': defaultdict(float),
                'daily_entries': defaultdict(int)
            }, data.get('weekly_stats', {}))
        except FileNotFoundError:
            pass
    
    def registrar_entrada(self, dni, nombre):
        """Registra la entrada de un empleado"""
        ahora = datetime.now()
        self.active_shifts[dni] = {
            'nombre': nombre,
            'entrada': ahora
        }
        self.save_data()
        return ahora
    
    def registrar_salida(self, dni, nombre):
        """Registra la salida de un empleado y calcula las horas"""
        if dni not in self.active_shifts:
            return None
        
        entrada = self.active_shifts[dni]['entrada']
        salida = datetime.now()
        horas_trabajadas = (salida - entrada).total_seconds() / 3600
        
        # Guardar en registros diarios
        fecha_str = entrada.strftime('%Y-%m-%d')
        self.daily_records[fecha_str][dni].append({
            'entrada': entrada,
            'salida': salida,
            'horas': horas_trabajadas,
            'nombre': nombre
        })
        
        # Actualizar estadísticas semanales
        self.weekly_stats[dni]['nombre'] = nombre
        self.weekly_stats[dni]['total_horas'] += horas_trabajadas
        self.weekly_stats[dni]['total_entradas'] += 1
        self.weekly_stats[dni]['daily_hours'][fecha_str] += horas_trabajadas
        self.weekly_stats[dni]['daily_entries'][fecha_str] += 1
        
        # Remover del turno activo
        del self.active_shifts[dni]
        self.save_data()
        
        return {
            'entrada': entrada,
            'salida': salida,
            'horas': horas_trabajadas
        }

tracker = ShiftTracker()

@bot.event
async def on_ready():
    print(f'{bot.user} está conectado!')
    tracker.load_data()
    weekly_reset.start()
    
    # Verificar que los canales existen
    canal_servicio = bot.get_channel(CANAL_SERVICIOAPP)
    canal_comandos = bot.get_channel(CANAL_COMANDOS)
    
    if canal_servicio:
        print(f'✅ Canal ServicioAPP encontrado: {canal_servicio.name}')
    else:
        print(f'❌ No se pudo encontrar el canal ServicioAPP (ID: {CANAL_SERVICIOAPP})')
    
    if canal_comandos:
        print(f'✅ Canal de comandos encontrado: {canal_comandos.name}')
    else:
        print(f'❌ No se pudo encontrar el canal de comandos (ID: {CANAL_COMANDOS})')
    
    # Enviar mensaje de inicio al canal de comandos
    if canal_comandos:
        embed = discord.Embed(
            title="🤖 Bot de Control de Horarios Iniciado",
            description="El bot está listo para registrar entradas y salidas.",
            color=discord.Color.green()
        )
        embed.add_field(name="Comandos Disponibles", value="`!hoy` `!semana` `!activos`", inline=False)
        await canal_comandos.send(embed=embed)

@bot.event
async def on_message(message):
    # Ignorar mensajes del propio bot
    if message.author == bot.user:
        return
    
    # Solo procesar mensajes del bot ServicioAPP en el canal específico
    if message.author.name == 'ServicioAPP' and message.channel.id == CANAL_SERVICIOAPP:
        # Obtener el canal de comandos para enviar las notificaciones
        canal_comandos = bot.get_channel(CANAL_COMANDOS)
        
        if not canal_comandos:
            print(f"⚠️ No se pudo encontrar el canal de comandos")
            return
        
        # Patrón para extraer DNI y nombre
        patron_entrada = r'\[PDA(\d+)\]\s+([^h]+)\s+ha entrado en servicio'
        patron_salida = r'\[PDA(\d+)\]\s+([^h]+)\s+ha salido de servicio'
        
        match_entrada = re.search(patron_entrada, message.content)
        match_salida = re.search(patron_salida, message.content)
        
        if match_entrada:
            dni = match_entrada.group(1)
            nombre = match_entrada.group(2).strip()
            entrada = tracker.registrar_entrada(dni, nombre)
            
            embed = discord.Embed(
                title="✅ Entrada Registrada",
                color=discord.Color.green(),
                timestamp=entrada
            )
            embed.add_field(name="DNI", value=f"PDA{dni}", inline=True)
            embed.add_field(name="Nombre", value=nombre, inline=True)
            embed.add_field(name="Hora", value=entrada.strftime('%H:%M:%S'), inline=True)
            
            await canal_comandos.send(embed=embed)
        
        elif match_salida:
            dni = match_salida.group(1)
            nombre = match_salida.group(2).strip()
            turno = tracker.registrar_salida(dni, nombre)
            
            if turno:
                embed = discord.Embed(
                    title="🔴 Salida Registrada",
                    color=discord.Color.red(),
                    timestamp=turno['salida']
                )
                embed.add_field(name="DNI", value=f"PDA{dni}", inline=True)
                embed.add_field(name="Nombre", value=nombre, inline=True)
                embed.add_field(name="Entrada", value=turno['entrada'].strftime('%H:%M:%S'), inline=True)
                embed.add_field(name="Salida", value=turno['salida'].strftime('%H:%M:%S'), inline=True)
                embed.add_field(name="Horas trabajadas", value=f"{turno['horas']:.2f}h", inline=True)
                
                await canal_comandos.send(embed=embed)
    
    # Procesar comandos solo en el canal de comandos
    if message.channel.id == CANAL_COMANDOS:
        await bot.process_commands(message)

@bot.command(name='hoy')
async def reporte_diario(ctx, dni=None):
    """Muestra el reporte de horas del día actual"""
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    
    if dni:
        # Reporte individual
        dni = dni.replace('PDA', '')
        records = tracker.daily_records[fecha_hoy].get(dni, [])
        
        if not records and dni not in tracker.active_shifts:
            await ctx.send(f"No hay registros para el DNI PDA{dni} hoy.")
            return
        
        nombre = records[0]['nombre'] if records else tracker.active_shifts[dni]['nombre']
        total_horas = sum(r['horas'] for r in records)
        entradas = len(records)
        
        embed = discord.Embed(
            title=f"📊 Reporte Diario - {nombre}",
            description=f"DNI: PDA{dni}\nFecha: {fecha_hoy}",
            color=discord.Color.blue()
        )
        
        for i, turno in enumerate(records, 1):
            entrada_str = turno['entrada'].strftime('%H:%M:%S')
            salida_str = turno['salida'].strftime('%H:%M:%S') if turno['salida'] else 'En curso'
            embed.add_field(
                name=f"Turno {i}",
                value=f"🕐 {entrada_str} → {salida_str}\n⏱️ {turno['horas']:.2f}h",
                inline=False
            )
        
        # Verificar si está en turno activo
        if dni in tracker.active_shifts:
            entrada_activa = tracker.active_shifts[dni]['entrada']
            tiempo_actual = (datetime.now() - entrada_activa).total_seconds() / 3600
            embed.add_field(
                name="🟢 Turno Actual (En curso)",
                value=f"🕐 {entrada_activa.strftime('%H:%M:%S')} → Ahora\n⏱️ {tiempo_actual:.2f}h",
                inline=False
            )
        
        embed.add_field(name="Total de Horas", value=f"{total_horas:.2f}h", inline=True)
        embed.add_field(name="Veces Entró", value=str(entradas), inline=True)
        
        await ctx.send(embed=embed)
    else:
        # Reporte general
        embed = discord.Embed(
            title="📊 Reporte Diario - Todos los Empleados",
            description=f"Fecha: {fecha_hoy}",
            color=discord.Color.blue()
        )
        
        records = tracker.daily_records[fecha_hoy]
        if not records and not tracker.active_shifts:
            await ctx.send("No hay registros para hoy.")
            return
        
        # Empleados con registros
        for dni, turnos in sorted(records.items()):
            if turnos:
                nombre = turnos[0]['nombre']
                total_horas = sum(t['horas'] for t in turnos)
                entradas = len(turnos)
                embed.add_field(
                    name=f"{nombre} (PDA{dni})",
                    value=f"⏱️ {total_horas:.2f}h | 🔄 {entradas} entradas",
                    inline=False
                )
        
        # Empleados actualmente en servicio
        if tracker.active_shifts:
            embed.add_field(name="━━━━━━━━━━━━━━━", value="**🟢 Actualmente en Servicio:**", inline=False)
            for dni, info in tracker.active_shifts.items():
                tiempo = (datetime.now() - info['entrada']).total_seconds() / 3600
                embed.add_field(
                    name=f"{info['nombre']} (PDA{dni})",
                    value=f"⏱️ {tiempo:.2f}h (en curso)",
                    inline=False
                )
        
        await ctx.send(embed=embed)

@bot.command(name='semana')
async def reporte_semanal(ctx, dni=None):
    """Muestra el reporte de horas de la semana"""
    if dni:
        dni = dni.replace('PDA', '')
        stats = tracker.weekly_stats.get(dni)
        
        if not stats or stats['total_horas'] == 0:
            await ctx.send(f"No hay registros para el DNI PDA{dni} esta semana.")
            return
        
        embed = discord.Embed(
            title=f"📈 Reporte Semanal - {stats['nombre']}",
            description=f"DNI: PDA{dni}",
            color=discord.Color.purple()
        )
        
        # Desglose por día
        for fecha, horas in sorted(stats['daily_hours'].items()):
            entradas = stats['daily_entries'][fecha]
            embed.add_field(
                name=fecha,
                value=f"⏱️ {horas:.2f}h | 🔄 {entradas} entradas",
                inline=False
            )
        
        embed.add_field(name="━━━━━━━━━━━━━━━", value="**Totales de la Semana:**", inline=False)
        embed.add_field(name="Total Horas", value=f"{stats['total_horas']:.2f}h", inline=True)
        embed.add_field(name="Total Entradas", value=str(stats['total_entradas']), inline=True)
        
        await ctx.send(embed=embed)
    else:
        # Reporte general semanal
        embed = discord.Embed(
            title="📈 Reporte Semanal - Todos los Empleados",
            color=discord.Color.purple()
        )
        
        if not tracker.weekly_stats:
            await ctx.send("No hay registros para esta semana.")
            return
        
        # Ordenar por horas trabajadas
        sorted_stats = sorted(
            tracker.weekly_stats.items(),
            key=lambda x: x[1]['total_horas'],
            reverse=True
        )
        
        for dni, stats in sorted_stats:
            if stats['total_horas'] > 0:
                embed.add_field(
                    name=f"{stats['nombre']} (PDA{dni})",
                    value=f"⏱️ {stats['total_horas']:.2f}h | 🔄 {stats['total_entradas']} entradas",
                    inline=False
                )
        
        await ctx.send(embed=embed)

@bot.command(name='activos')
async def empleados_activos(ctx):
    """Muestra los empleados actualmente en servicio"""
    if not tracker.active_shifts:
        await ctx.send("No hay empleados en servicio actualmente.")
        return
    
    embed = discord.Embed(
        title="🟢 Empleados en Servicio",
        color=discord.Color.green()
    )
    
    for dni, info in tracker.active_shifts.items():
        tiempo = datetime.now() - info['entrada']
        horas = tiempo.total_seconds() / 3600
        embed.add_field(
            name=f"{info['nombre']} (PDA{dni})",
            value=f"🕐 Entrada: {info['entrada'].strftime('%H:%M:%S')}\n⏱️ Tiempo: {horas:.2f}h",
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='reset_semana')
@commands.has_permissions(administrator=True)
async def reset_semanal(ctx):
    """Resetea las estadísticas semanales (solo administradores)"""
    tracker.weekly_stats.clear()
    tracker.save_data()
    await ctx.send("✅ Estadísticas semanales reseteadas.")

@tasks.loop(hours=168)  # 7 días
async def weekly_reset():
    """Resetea automáticamente las estadísticas cada semana"""
    tracker.weekly_stats.clear()
    tracker.save_data()

# Token del bot
TOKEN = 'MTQ0ODg2MjUxMTQ3NzU1OTM0Nw.G7FlTH.I2hKYIwp7JYHReSZoqBT7z-AheHV2MrDzrGGSY'
bot.run(TOKEN)